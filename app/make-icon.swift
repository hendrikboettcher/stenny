// Draws Stenny's app icon (the website's stick figure in ink on a white rounded square, drawn bolder than
// on the site so it reads as an icon) into an .iconset folder, on Apple's macOS icon template (shape 824 of
// 1024, nothing outside it). build.sh turns it into AppIcon.icns.
// Usage: swift make-icon.swift <out.iconset>
import AppKit

let ink = NSColor(srgbRed: 0x1d / 255, green: 0x1b / 255, blue: 0x18 / 255, alpha: 1)    // --ink
let white = NSColor.white
let paperShade = NSColor(srgbRed: 0xf3 / 255, green: 0xef / 255, blue: 0xe9 / 255, alpha: 1)  // --bar
let edge = NSColor(srgbRed: 0xdd / 255, green: 0xd6 / 255, blue: 0xcc / 255, alpha: 1)        // --line

func figure() -> NSBezierPath {  // same as StennyFigure in Sources/Stenny/MenuBarImage.swift (viewBox 120×160, y down)
    let p = NSBezierPath(ovalIn: NSRect(x: 49, y: 11, width: 22, height: 22))
    for line in [[(60, 34), (60, 84)], [(60, 34), (50, 58), (46, 80)], [(60, 34), (72, 60), (90, 62)],
                 [(60, 84), (54, 114), (50, 144)], [(60, 84), (66, 114), (70, 144)]] {
        p.move(to: NSPoint(x: line[0].0, y: line[0].1))
        for pt in line.dropFirst() { p.line(to: NSPoint(x: pt.0, y: pt.1)) }
    }
    return p
}

func png(size: Int) -> Data {
    let s = CGFloat(size)
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    // macOS icon grid: the shape fills 824/1024 of the canvas, corner radius ~185/1024
    let inset = s * 100 / 1024
    let body = NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
    let shape = NSBezierPath(roundedRect: body, xRadius: s * 185 / 1024, yRadius: s * 185 / 1024)
    // Apple's guidance: a simple background (solid or gradient) and no baked-in shadows, glows or bevels; the system
    // draws its own. Nothing may reach past the shape either: macOS 26 boxes such icons in gray.
    NSGradient(starting: white, ending: paperShade)!.draw(in: shape, angle: -90)   // white to a hint of paper
    edge.setStroke()                       // a hairline inside the shape, so white keeps its outline on a light Dock
    let inner = NSBezierPath(roundedRect: body.insetBy(dx: s / 1024, dy: s / 1024),
                             xRadius: s * 184 / 1024, yRadius: s * 184 / 1024)
    inner.lineWidth = max(1, s * 2 / 1024)
    inner.stroke()
    // The figure, 75 % of the shape's height: big enough to read, with the breathing room the grid asks for.
    // Optically centred: the body line sits near the middle (the pen arm reaches right), a touch below the
    // geometric centre because the head carries most of the weight.
    let height: CGFloat = 152                   // the figure's height in the logo's units (y 2–154)
    let scale = body.height * 0.75 / height
    let t = NSAffineTransform()
    t.translateX(by: s / 2, yBy: s / 2)
    t.scaleX(by: scale, yBy: -scale)
    t.translateX(by: -64, yBy: -75)
    // The website's line figure with its outlined head, a little heavier (8.5 units, the site draws 7) so it
    // holds up from the Dock down to 16 px; rounded ends and joints, as Apple advises against sharp corners.
    let path = figure()
    path.transform(using: t as AffineTransform)
    path.lineWidth = 8.5 * scale
    path.lineCapStyle = .round
    path.lineJoinStyle = .round
    ink.setStroke()
    path.stroke()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let out = URL(fileURLWithPath: CommandLine.arguments[1])
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    try! png(size: base).write(to: out.appendingPathComponent("icon_\(base)x\(base).png"))
    try! png(size: base * 2).write(to: out.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}
