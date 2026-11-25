import SwiftUI

/// The live log window: what the always-on mic heard and, step by step, what she made of it. Newest at the top.
struct ThoughtLogView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        let voice = state.voice
        let log = voice.thoughts
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Live log").font(.title2.weight(.semibold))
                    Text(status(voice)).font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Clear") { log.clear() }.disabled(log.entries.isEmpty)
            }
            .padding(16)
            Divider()
            if log.entries.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "waveform").font(.largeTitle).foregroundStyle(.tertiary)
                    Text(voice.ambient == .off ? "Always listening is off. Turn it on in Settings to see her think here."
                                               : "Say something. Each phrase she hears shows up here with what she decided and why.")
                        .multilineTextAlignment(.center).foregroundStyle(.secondary).frame(maxWidth: 320)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(log.entries) { EntryRow(entry: $0) }
                    }
                    .padding(16)
                }
            }
            Divider()
            Text("Kept in memory only. Nothing here is saved to disk, and it's gone when EDITH quits.")
                .font(.caption).foregroundStyle(.secondary).padding(.horizontal, 16).padding(.vertical, 8)
        }
        .frame(minWidth: 420, minHeight: 360)
    }

    private func status(_ v: VoiceVM) -> String {
        let mode: String
        switch v.ambient {
        case .judge: mode = "Just talk, listening"
        case .name: mode = "Her name only, listening"
        case .off: mode = VoiceVM.ambientWanted == .off ? "Always listening is off" : "Paused, comes back by itself"
        }
        let ears = EarsModel.current == .fluidMini ? "Fluid Speech Mini" : EarsModel.current == .phonon25 ? "Phonon-2.5" : "Phonon-2"
        return "\(mode) · ears: \(ears)"
    }
}

private struct EntryRow: View {
    let entry: ThoughtLog.Entry

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(entry.at, format: .dateTime.hour().minute().second())
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                Text(entry.verdict.label.uppercased())
                    .font(.caption2.weight(.bold)).padding(.horizontal, 6).padding(.vertical, 2)
                    .background(tint.opacity(0.18), in: Capsule()).foregroundStyle(tint)
                Spacer()
            }
            Text("\u{201C}\(entry.heard)\u{201D}").font(.body.weight(.medium)).textSelection(.enabled)
            ForEach(entry.steps) { s in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Image(systemName: "arrow.turn.down.right").font(.caption2).foregroundStyle(.tertiary)
                    Text(s.text).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                }
            }
            if !entry.result.isEmpty {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Image(systemName: icon).font(.caption).foregroundStyle(tint)
                    Text(entry.result).font(.callout).textSelection(.enabled)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
    }

    private var tint: Color {
        switch entry.verdict {
        case .answered: .blue
        case .typed: .green
        case .working: .orange
        case .ignored, .noise: .secondary
        }
    }

    private var icon: String {
        switch entry.verdict {
        case .answered: "bubble.left.fill"
        case .typed: "keyboard.fill"
        case .working: "ellipsis"
        case .ignored: "moon.zzz.fill"
        case .noise: "speaker.slash"
        }
    }
}
