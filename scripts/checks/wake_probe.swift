// Runs the app's own wake word spotter (Core/Voice/WakeWord.swift, the same sherpa-onnx library and model) over audio files and
// prints one JSON line per file: seconds, detections (seconds into the file) and the CPU the spotter itself used.
// Built and called by scripts/checks/wake_false.py; usage: wake_probe [--home DIR] [--sens S | --score X --thr Y] [--word W] files...
import AVFoundation
import Foundation

func cpuSeconds() -> Double {
    var u = rusage(); getrusage(RUSAGE_SELF, &u)
    return Double(u.ru_utime.tv_sec) + Double(u.ru_utime.tv_usec) / 1e6 + Double(u.ru_stime.tv_sec) + Double(u.ru_stime.tv_usec) / 1e6
}

@main
struct WakeProbe {
  static func main() {
    var home = NSHomeDirectory() + "/Misty", sens = 0.5, word = "edith", realtime = 0.0
    var score: Float?, thr: Float?
    var files: [String] = []
    var args = Array(CommandLine.arguments.dropFirst())
    while !args.isEmpty {
        let a = args.removeFirst()
        switch a {
        case "--home": home = args.removeFirst()
        case "--sens": sens = Double(args.removeFirst())!
        case "--score": score = Float(args.removeFirst())
        case "--thr": thr = Float(args.removeFirst())
        case "--word": word = args.removeFirst()
        case "--realtime": realtime = Double(args.removeFirst())!
        default: files.append(a)
        }
    }
    let settings = WakeSettings(enabled: true, word: word, sensitivity: sens)
    let tuning: (score: Float, threshold: Float)? = (score != nil && thr != nil) ? (score!, thr!) : nil
    let out16 = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!

    // --realtime N: what the app does while idle, for N seconds of wall clock. The file is played in as the mic would deliver it
    // (100 ms buffers, each converted to 16 kHz mono the way AudioIO does), paced by the clock; reports the whole process's CPU.
    if realtime > 0, let path = files.first, let f = try? AVAudioFile(forReading: URL(fileURLWithPath: path)),
       let spotter = SherpaSpotter(home: home, settings: settings, tuning: tuning) {
        let inF = f.processingFormat
        let mono = AVAudioFormat(standardFormatWithSampleRate: inF.sampleRate, channels: 1)!
        let conv = AVAudioConverter(from: mono, to: out16)!
        let per = AVAudioFrameCount(inF.sampleRate / 10)
        let wall0 = Date(), cpu0 = cpuSeconds()
        var n = 0
        let stream = WakeStream(spotter: spotter, gated: ProcessInfo.processInfo.environment["WAKE_PROBE_UNGATED"] == nil) { }   // the app's own path, activity gate included
        while Date().timeIntervalSince(wall0) < realtime {
            guard let src = AVAudioPCMBuffer(pcmFormat: inF, frameCapacity: per) else { break }
            do { try f.read(into: src, frameCount: per) } catch { break }
            if src.frameLength == 0 { f.framePosition = 0; continue }
            let one = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: src.frameLength)!
            one.frameLength = src.frameLength
            one.floatChannelData![0].update(from: src.floatChannelData![0], count: Int(src.frameLength))
            let dst = AVAudioPCMBuffer(pcmFormat: out16, frameCapacity: AVAudioFrameCount(Double(src.frameLength) * 16000 / inF.sampleRate + 32))!
            var fed = false
            if ProcessInfo.processInfo.environment["WAKE_PROBE_NO_CONVERT"] == nil {   // =1: skip the conversion, to see what it costs
                conv.convert(to: dst, error: nil) { _, st in if fed { st.pointee = .noDataNow; return nil }; fed = true; st.pointee = .haveData; return one }
            }
            let samples = Array(UnsafeBufferPointer(start: dst.floatChannelData![0], count: Int(dst.frameLength)))
            if ProcessInfo.processInfo.environment["WAKE_PROBE_NO_SPOTTER"] == nil { stream.feed(samples) }   // =1: skip the spotter
            n += 1
            let due = wall0.addingTimeInterval(Double(n) * 0.1)
            Thread.sleep(forTimeInterval: max(0, due.timeIntervalSinceNow))
        }
        let wall = Date().timeIntervalSince(wall0), cpu = cpuSeconds() - cpu0
        print("{\"realtime_s\":\(String(format: "%.1f", wall)),\"cpu_s\":\(String(format: "%.2f", cpu)),\"cpu_percent_of_one_core\":\(String(format: "%.2f", 100 * cpu / wall))}")
        return
    }
    for path in files {
        guard let spotter = SherpaSpotter(home: home, settings: settings, tuning: tuning) else { print("{\"error\":\"no spotter\"}"); exit(2) }
        guard let f = try? AVAudioFile(forReading: URL(fileURLWithPath: path)) else { print("{\"file\":\"\(path)\",\"error\":\"unreadable\"}"); continue }
        let inF = f.processingFormat
        let mono = AVAudioFormat(standardFormatWithSampleRate: inF.sampleRate, channels: 1)!
        let conv = AVAudioConverter(from: mono, to: out16)!
        let stream = WakeStream(spotter: spotter, gated: ProcessInfo.processInfo.environment["WAKE_PROBE_UNGATED"] == nil) { }
        var done: Double = 0, cpu = 0.0, hits: [Double] = [], total = 0
        let chunk: AVAudioFrameCount = 48000
        while f.framePosition < f.length {
            guard let src = AVAudioPCMBuffer(pcmFormat: inF, frameCapacity: chunk) else { break }
            do { try f.read(into: src, frameCount: chunk) } catch { break }
            if src.frameLength == 0 { break }
            let one = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: src.frameLength)!
            one.frameLength = src.frameLength
            one.floatChannelData![0].update(from: src.floatChannelData![0], count: Int(src.frameLength))
            let dst = AVAudioPCMBuffer(pcmFormat: out16, frameCapacity: AVAudioFrameCount(Double(src.frameLength) * 16000 / inF.sampleRate + 64))!
            var fed = false
            conv.convert(to: dst, error: nil) { _, st in if fed { st.pointee = .noDataNow; return nil }; fed = true; st.pointee = .haveData; return one }
            let samples = Array(UnsafeBufferPointer(start: dst.floatChannelData![0], count: Int(dst.frameLength)))
            // the app feeds the spotter in the mic's own small pieces; do the same
            var i = 0
            while i < samples.count {
                let piece = Array(samples[i..<min(i + 1600, samples.count)])
                let t0 = cpuSeconds()
                total += piece.count
                stream.feed(piece)
                cpu += cpuSeconds() - t0
                if stream.hasFired { hits.append(Double(total) / 16000); stream.clearTail() }   // counted, then listening again
                i += 1600
            }
            done = Double(total) / 16000
        }
        print("{\"file\":\"\(path)\",\"seconds\":\(String(format: "%.1f", done)),\"hits\":\(hits.map { String(format: "%.1f", $0) }),\"rejected\":\(stream.rejected),\"cpu_s\":\(String(format: "%.2f", cpu))}")
        fflush(stdout)
    }
  }
}
