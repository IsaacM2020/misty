import AudioToolbox
import AVFoundation
import CoreAudio
import Foundation

/// Everything the Mac plays, as 16 kHz mono Float32: the teacher on Zoom, Meet, Teams or a whiteboard app, whatever
/// the platform, because it listens to the Mac's sound and not to any app (the way Granola does). A Core Audio tap
/// on the default output (macOS asks once for "System Audio Recording"). Misty's own sound is left out. The audio is
/// only ever read into the ears; nothing is written to disk.
public final class SystemAudio: @unchecked Sendable {
    public enum Failure: Error, Equatable { case tap(Int32), device(Int32), format, proc(Int32), start(Int32) }

    private let q = DispatchQueue(label: "misty.systemaudio", qos: .userInitiated)
    private let out = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggID = AudioObjectID(kAudioObjectUnknown)
    private var proc: AudioDeviceIOProcID?
    private var onSamples: (@Sendable ([Float]) -> Void)?
    private var listener: AudioObjectPropertyListenerBlock?
    private var heardAnything = false                     // for `silentFor`
    private var startedAt = Date()
    private var restarting = false
    private var lastDeliveredAt = Date()
    public private(set) var running = false

    public init() {}

    /// Seconds since the tap last delivered anything, once it has gone quiet for 30 s (it delivers only while sound plays;
    /// echo cancellation on the mic once stopped its callbacks dead without an error), else nil.
    public var stalledFor: TimeInterval? {
        q.sync {
            guard running else { return nil }
            let s = Date().timeIntervalSince(lastDeliveredAt)
            return s > 30 ? s : nil
        }
    }

    /// Seconds since the tap started if it has delivered nothing but digital silence the whole time (macOS gives
    /// zeros when System Audio Recording is not allowed; a muted teacher looks the same), else nil.
    public var silentFor: TimeInterval? { q.sync { running && !heardAnything ? Date().timeIntervalSince(startedAt) : nil } }

    /// Start listening. `onSamples` gets about 10-20 chunks a second, off the main thread.
    public func start(onSamples: @escaping @Sendable ([Float]) -> Void) throws {
        try q.sync {
            guard !running else { return }
            self.onSamples = onSamples
            try open()
            running = true
            startedAt = Date()
            lastDeliveredAt = startedAt
            heardAnything = false
            watchOutputChange()
        }
    }

    public func stop() {
        q.sync {
            unwatchOutputChange()
            close()
            running = false
            onSamples = nil
        }
    }

    // MARK: the tap

    private func open() throws {
        // Misty's own voice stays out (she is silent in class, but a chime or an answer must not reach the transcript).
        var mine = AudioObjectID(kAudioObjectUnknown)
        var pid = getpid()
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyTranslatePIDToProcessObject,
                                              mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, UInt32(MemoryLayout<pid_t>.size), &pid, &size, &mine)

        let desc = CATapDescription(stereoGlobalTapButExcludeProcesses: mine == kAudioObjectUnknown ? [] : [mine])
        desc.uuid = UUID()
        desc.name = "Misty class"
        desc.isPrivate = true
        desc.muteBehavior = .unmuted                      // the class keeps playing for Isaac as normal
        var status = AudioHardwareCreateProcessTap(desc, &tapID)
        guard status == noErr else { throw Failure.tap(status) }

        let outUID = try Self.defaultOutputUID()
        let agg: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Misty class tap",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: outUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outUID]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true, kAudioSubTapUIDKey: desc.uuid.uuidString]],
        ]
        status = AudioHardwareCreateAggregateDevice(agg as CFDictionary, &aggID)
        guard status == noErr else { close(); throw Failure.device(status) }

        // the tap's own format (usually 48 kHz stereo Float32), turned into 16 kHz mono for the ears
        var fmtAddr = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyFormat, mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        var asbd = AudioStreamBasicDescription()
        var asbdSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        status = AudioObjectGetPropertyData(tapID, &fmtAddr, 0, nil, &asbdSize, &asbd)
        guard status == noErr, let inFormat = AVAudioFormat(streamDescription: &asbd),
              let conv = AVAudioConverter(from: inFormat, to: out) else { close(); throw Failure.format }

        let outFormat = out
        let sink: @Sendable ([Float]) -> Void = { [weak self] s in self?.deliver(s) }
        status = AudioDeviceCreateIOProcIDWithBlock(&proc, aggID, q) { _, inData, _, _, _ in
            guard let buf = AVAudioPCMBuffer(pcmFormat: inFormat, bufferListNoCopy: inData, deallocator: nil),
                  buf.frameLength > 0 else { return }
            let cap = AVAudioFrameCount(Double(buf.frameLength) * outFormat.sampleRate / inFormat.sampleRate + 32)
            guard let dst = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: cap) else { return }
            var fed = false
            var err: NSError?
            conv.convert(to: dst, error: &err) { _, st in
                if fed { st.pointee = .noDataNow; return nil }
                fed = true
                st.pointee = .haveData
                return buf
            }
            guard err == nil, dst.frameLength > 0, let p = dst.floatChannelData else { return }
            sink(Array(UnsafeBufferPointer(start: p[0], count: Int(dst.frameLength))))
        }
        guard status == noErr, let proc else { close(); throw Failure.proc(status) }
        status = AudioDeviceStart(aggID, proc)
        guard status == noErr else { close(); throw Failure.start(status) }
    }

    private func deliver(_ s: [Float]) {
        lastDeliveredAt = Date()
        if !heardAnything, s.contains(where: { abs($0) > 0.0001 }) { heardAnything = true }
        onSamples?(s)
    }

    private func close() {
        if let proc, aggID != kAudioObjectUnknown {
            AudioDeviceStop(aggID, proc)
            AudioDeviceDestroyIOProcID(aggID, proc)
        }
        proc = nil
        if aggID != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(aggID); aggID = AudioObjectID(kAudioObjectUnknown) }
        if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID); tapID = AudioObjectID(kAudioObjectUnknown) }
    }

    static func defaultOutputUID() throws -> String {
        var dev = AudioObjectID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultSystemOutputDevice,
                                              mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var st = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &dev)
        guard st == noErr, dev != kAudioObjectUnknown else { throw Failure.device(st) }
        var uidAddr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyDeviceUID, mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        var uid: Unmanaged<CFString>?
        var usize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        st = AudioObjectGetPropertyData(dev, &uidAddr, 0, nil, &usize, &uid)
        guard st == noErr, let u = uid?.takeRetainedValue() else { throw Failure.device(st) }
        return u as String
    }

    // MARK: AirPods in, speakers out: follow the default output

    private func watchOutputChange() {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultSystemOutputDevice,
                                              mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in self?.outputChanged() }
        listener = block
        AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addr, q, block)
    }

    private func unwatchOutputChange() {
        guard let block = listener else { return }
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultSystemOutputDevice,
                                              mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        AudioObjectRemovePropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addr, q, block)
        listener = nil
    }

    /// The output device changed mid-class: rebuild the tap on the new one so the teacher isn't lost.
    private func outputChanged() {
        guard running, !restarting else { return }
        restarting = true
        q.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self, self.running else { return }
            self.close()
            do { try self.open() } catch { self.running = false }
            self.restarting = false
        }
    }
}
