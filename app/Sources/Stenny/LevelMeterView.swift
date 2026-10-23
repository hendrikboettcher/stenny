import AppKit

/// Input level meter for the menu: a bar from -60 dB (silence) to 0 dB (the microphone's maximum),
/// orange below -50 dB (too quiet to transcribe well: move the microphone closer), red near clipping.
/// `show(_:)` sets the measured level; the bar follows it smoothly like a hardware meter
/// (rises quickly, falls at about 25 dB per second).
final class LevelMeterView: NSView {
    static let floor = -60.0, quiet = -50.0, loud = -6.0
    private var target = floor
    private var shown = floor
    private var lastStep = Date()

    override init(frame: NSRect) {
        super.init(frame: frame)
    }

    required init?(coder: NSCoder) { fatalError() }

    func show(_ db: Double) {
        target = max(Self.floor, min(0, db))
    }

    /// One animation frame; returns true if the bar moved.
    func step() -> Bool {
        let dt = min(0.2, Date().timeIntervalSince(lastStep))
        lastStep = Date()
        let before = shown
        if target > shown {
            shown += (target - shown) * min(1, dt * 25)   // attack ≈ 40 ms
        } else {
            shown = max(target, shown - 25 * dt)          // release 25 dB/s
        }
        if abs(shown - before) > 0.01 { needsDisplay = true; return true }
        return false
    }

    override func draw(_ dirtyRect: NSRect) {
        let label = "Input" as NSString
        let font = NSFont.menuFont(ofSize: 0)
        let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.secondaryLabelColor]
        let mono: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: font.pointSize, weight: .regular),
            .foregroundColor: NSColor.secondaryLabelColor]
        let midY = bounds.midY
        let labelSize = label.size(withAttributes: attrs)
        label.draw(at: NSPoint(x: 21, y: midY - labelSize.height / 2), withAttributes: attrs)

        let value = String(format: "%.0f dB", target) as NSString
        let valueWidth = ("-120 dB" as NSString).size(withAttributes: mono).width
        let valueSize = value.size(withAttributes: mono)
        value.draw(at: NSPoint(x: bounds.maxX - 14 - valueSize.width, y: midY - valueSize.height / 2), withAttributes: mono)

        let track = NSRect(x: 21 + labelSize.width + 10, y: midY - 3.5,
                           width: bounds.width - (21 + labelSize.width + 10) - 14 - valueWidth - 10, height: 7)
        NSColor.quaternaryLabelColor.setFill()
        NSBezierPath(roundedRect: track, xRadius: 3.5, yRadius: 3.5).fill()

        let fraction = (shown - Self.floor) / -Self.floor
        guard fraction > 0.005 else { return }
        var fill = track
        fill.size.width = max(track.height, track.width * fraction)
        (shown < Self.quiet ? NSColor.systemOrange : shown > Self.loud ? NSColor.systemRed : NSColor.systemGreen).setFill()
        NSBezierPath(roundedRect: fill, xRadius: 3.5, yRadius: 3.5).fill()

        // tick at the "quiet" threshold, so it's visible where good starts
        let x = track.minX + track.width * (Self.quiet - Self.floor) / -Self.floor
        NSColor.tertiaryLabelColor.setFill()
        NSRect(x: x, y: track.minY - 2, width: 1, height: track.height + 4).fill()
    }
}
