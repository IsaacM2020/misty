import Foundation

/// DEV ONLY switch for which model reads her phrases (Isaac's own Mac, shown under the misty.devTools flag). The default
/// stays Phonon-2 on the Neural Engine, which is the only live one. The other two already sit on this Mac and read each
/// finished phrase in a child process (about 1.5 s and 7 s), so they give the final text, not the live partials.
public enum EarsModel: String, CaseIterable, Sendable {
    case phonon2, phonon25, fluidMini
    static let key = "misty.earsModel"

    public static var current: EarsModel {
        // Fluid Speech Mini reads every finished phrase unless he picked another model. Without it installed, Phonon-2 as before.
        get { EarsModel(rawValue: UserDefaults.standard.string(forKey: key) ?? "") ?? (EarsModel.fluidMini.isInstalled ? .fluidMini : .phonon2) }
        set { UserDefaults.standard.set(newValue.rawValue, forKey: key) }
    }

    public var label: String {
        switch self {
        case .phonon2: "Phonon-2 Core ML (live, current)"
        case .phonon25: "Phonon-2.5 English via fermion (not live, about 7 s a phrase)"
        case .fluidMini: "Fluid Speech Mini ONNX (not live, about 0.3 s a phrase once warm)"
        }
    }

    static let support = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Misty")
    var miniHome: URL { Self.support.appendingPathComponent("fluid-mini") }
    private var fermionPython: URL { Self.support.appendingPathComponent("fermion/bin/python") }

    /// Only what actually runs on this Mac.
    public static func available() -> [EarsModel] { allCases.filter { $0.isInstalled } }

    public var isInstalled: Bool {
        let fm = FileManager.default
        switch self {
        case .phonon2: return true
        case .phonon25: return fm.isExecutableFile(atPath: fermionPython.path)
        case .fluidMini:
            return fm.isExecutableFile(atPath: miniHome.appendingPathComponent("venv/bin/python").path)
                && fm.fileExists(atPath: miniHome.appendingPathComponent("model/encoder.onnx").path)
        }
    }

    /// Start Fluid Speech Mini's worker now (model loaded in the background), so the first phrase does not pay for it.
    public static func warmMini() {
        guard current == .fluidMini, fluidMini.isInstalled else { return }
        DispatchQueue.global(qos: .utility).async { MiniWorker.shared.warm(home: fluidMini.miniHome) }
    }

    /// Reads a 16 kHz mono phrase. Fluid Speech Mini goes through one long-lived worker (the model stays loaded);
    /// the others run in a child process per phrase. nil on any failure (the caller then falls back to Phonon-2); "" when the
    /// model read it fine and heard no words (a cough, the room), which closes the phrase instead of asking Phonon-2 to guess.
    func read(_ wave: [Float]) -> String? {
        guard self != .phonon2, isInstalled else { return nil }
        let wav = FileManager.default.temporaryDirectory.appendingPathComponent("misty-ears-\(UUID().uuidString).wav")
        guard (try? Self.wavData(wave).write(to: wav)) != nil else { return nil }
        defer { try? FileManager.default.removeItem(at: wav) }
        if self == .fluidMini, MiniWorker.available(home: miniHome) {
            return MiniWorker.shared.read(wav: wav, home: miniHome)
        }
        let p = Process()
        var env = ProcessInfo.processInfo.environment
        env["HF_HUB_OFFLINE"] = "1"                                // no downloads, ever
        p.environment = env
        switch self {
        case .fluidMini:
            p.executableURL = miniHome.appendingPathComponent("venv/bin/python")
            p.arguments = [miniHome.appendingPathComponent("transcribe.py").path, miniHome.appendingPathComponent("model").path, wav.path]
        case .phonon25:
            p.executableURL = fermionPython
            p.arguments = ["-c", "import sys; from fermion.cli import main; sys.exit(main())", "transcribe", "phonon-2.5-en", wav.path]
        case .phonon2: return nil
        }
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return nil }
        let timer = DispatchWorkItem { if p.isRunning { p.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + 60, execute: timer)
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        timer.cancel()
        guard p.terminationStatus == 0 else { return nil }
        let text = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? nil : text
    }

    static func wavData(_ wave: [Float]) -> Data {
        var d = Data()
        func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        let n = UInt32(wave.count * 2)
        d.append(contentsOf: Array("RIFF".utf8)); u32(36 + n); d.append(contentsOf: Array("WAVEfmt ".utf8))
        u32(16); u16(1); u16(1); u32(16000); u32(32000); u16(2); u16(16)
        d.append(contentsOf: Array("data".utf8)); u32(n)
        for x in wave { u16(UInt16(bitPattern: Int16(max(-1, min(1, x)) * 32767))) }
        return d
    }
}


/// Fluid Speech Mini as one long-lived child process (`serve.py`): it loads the ONNX model once and then answers one wav path per
/// line, so a phrase no longer pays ~0.45 s of Python start and model load (about 1.3 s became about 0.3 s for a 4 s phrase).
/// One read at a time. Any failure kills the process; the next read starts a fresh one. It stops by itself after 10 quiet minutes
/// (the model is ~300 MB) and when the app ends (its stdin closes).
final class MiniWorker: @unchecked Sendable {
    static let shared = MiniWorker()
    static func available(home: URL) -> Bool { FileManager.default.fileExists(atPath: home.appendingPathComponent("serve.py").path) }

    private let gate = NSLock()                    // one request at a time, and start/stop
    private let bufLock = NSLock()
    private var buf = Data()
    private let wake = DispatchSemaphore(value: 0)
    private var proc: Process?
    private var input: FileHandle?
    private var lastUsed = Date()
    private var idleCheck = false
    static let idleSeconds = 600.0
    static let loadTimeout = 60.0
    static let readTimeout = 30.0

    func warm(home: URL) {
        gate.lock(); defer { gate.unlock() }
        _ = ensureStarted(home: home)
    }

    func read(wav: URL, home: URL) -> String? {
        gate.lock(); defer { gate.unlock() }
        guard ensureStarted(home: home), let input else { return nil }
        lastUsed = Date()
        guard (try? input.write(contentsOf: Data((wav.path + "\n").utf8))) != nil else { stop(); return nil }
        guard let line = nextLine(timeout: Self.readTimeout),
              let obj = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any],
              let text = obj["text"] as? String else { stop(); return nil }
        lastUsed = Date()
        scheduleIdleStop()
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func ensureStarted(home: URL) -> Bool {
        if let p = proc, p.isRunning { return true }
        stop()
        let p = Process()
        p.executableURL = home.appendingPathComponent("venv/bin/python")
        p.arguments = [home.appendingPathComponent("serve.py").path, home.appendingPathComponent("model").path]
        var env = ProcessInfo.processInfo.environment
        env["HF_HUB_OFFLINE"] = "1"                                // no downloads, ever
        p.environment = env
        let out = Pipe(), inp = Pipe()
        p.standardOutput = out
        p.standardInput = inp
        p.standardError = FileHandle.nullDevice
        bufLock.lock(); buf = Data(); bufLock.unlock()
        out.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            guard let self else { return }
            if d.isEmpty { h.readabilityHandler = nil } else { self.bufLock.lock(); self.buf.append(d); self.bufLock.unlock() }
            self.wake.signal()
        }
        do { try p.run() } catch { return false }
        proc = p
        input = inp.fileHandleForWriting
        guard let first = nextLine(timeout: Self.loadTimeout), first.contains("ready") else { stop(); return false }
        lastUsed = Date()
        scheduleIdleStop()
        return true
    }

    /// Blocks until a whole line has arrived or the time is up. Only called with `gate` held.
    private func nextLine(timeout: Double) -> String? {
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            bufLock.lock()
            if let i = buf.firstIndex(of: 10) {
                let line = String(decoding: buf[buf.startIndex..<i], as: UTF8.self)
                buf.removeSubrange(buf.startIndex...i)
                bufLock.unlock()
                return line
            }
            bufLock.unlock()
            let left = deadline.timeIntervalSinceNow
            if left <= 0 || proc?.isRunning == false { return nil }
            _ = wake.wait(timeout: .now() + min(left, 0.5))
        }
    }

    private func stop() {
        if let p = proc {
            (p.standardOutput as? Pipe)?.fileHandleForReading.readabilityHandler = nil
            try? input?.close()
            if p.isRunning { p.terminate() }
        }
        proc = nil; input = nil
    }

    private func scheduleIdleStop() {
        guard !idleCheck else { return }
        idleCheck = true
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + Self.idleSeconds) { [weak self] in
            guard let self else { return }
            self.gate.lock(); defer { self.gate.unlock() }
            self.idleCheck = false
            if Date().timeIntervalSince(self.lastUsed) >= Self.idleSeconds { self.stop() } else { self.scheduleIdleStop() }
        }
    }
}
