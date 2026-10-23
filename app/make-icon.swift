// Draws Stenny's app icon (as in the website's notification mock-up: the light stick figure on an
// ink-coloured rounded square) into an .iconset folder. build.sh turns it into AppIcon.icns.
// Usage: swift make-icon.swift <out.iconset>
import AppKit

let ink = NSColor(srgbRed: 0x1d / 255, green: 0x1b / 255, blue: 0x18 / 255, alpha: 1)    // --ink
let paper = NSColor(srgbRed: 0xfb / 255, green: 0xfa / 255, blue: 0xf7 / 255, alpha: 1)  // --bg

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
    ink.setFill()
    NSBezierPath(roundedRect: body, xRadius: s * 185 / 1024, yRadius: s * 185 / 1024).fill()
    // the figure, 62 % of the shape's height, centred; flip because the path's y runs down
    let crop = NSRect(x: 36, y: 2, width: 64, height: 152)
    let scale = body.height * 0.62 / crop.height
    let t = NSAffineTransform()
    t.translateX(by: s / 2, yBy: s / 2)
    t.scaleX(by: scale, yBy: -scale)
    t.translateX(by: -crop.midX, yBy: -crop.midY)
    let path = figure()
    path.transform(using: t as AffineTransform)
    path.lineWidth = 7 * scale
    path.lineCapStyle = .round
    path.lineJoinStyle = .round
    paper.setStroke()
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
