import AppKit

/// Draws symbol + time as a single template image for the menu bar.
/// That way both are guaranteed to show, sit centered on one line,
/// and macOS tints the image automatically (light/dark).
enum MenuBarImage {
    static func make(symbol: String, text: String) -> NSImage {
        let fontSize = NSFont.menuBarFont(ofSize: 0).pointSize
        let font = NSFont.monospacedDigitSystemFont(ofSize: fontSize, weight: .regular)
        let config = NSImage.SymbolConfiguration(pointSize: fontSize, weight: .regular)
        let icon = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)?
            .withSymbolConfiguration(config) ?? NSImage()

        let textAttrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.black]
        let textSize = (text as NSString).size(withAttributes: textAttrs)
        let spacing: CGFloat = 4
        let height = max(icon.size.height, textSize.height)
        let size = NSSize(width: ceil(icon.size.width + spacing + textSize.width), height: ceil(height))

        let image = NSImage(size: size, flipped: false) { _ in
            // Center symbol and text vertically
            icon.draw(in: NSRect(x: 0, y: (height - icon.size.height) / 2,
                                 width: icon.size.width, height: icon.size.height))
            (text as NSString).draw(at: NSPoint(x: icon.size.width + spacing,
                                                y: (height - textSize.height) / 2),
                                    withAttributes: textAttrs)
            return true
        }
        image.isTemplate = true
        return image
    }

    /// Stenny's stick figure (the website's logo, viewBox 120×160), as a template image for the idle menu bar.
    /// Template images are drawn black on light and white on dark menu bars by macOS.
    static func figure(height: CGFloat = 17) -> NSImage {
        let crop = NSRect(x: 36, y: 2, width: 64, height: 152)   // the figure's bounds in the logo's coordinates
        let scale = height / crop.height
        let image = NSImage(size: NSSize(width: ceil(crop.width * scale), height: height), flipped: true) { _ in
            let t = NSAffineTransform()
            t.scale(by: scale)
            t.translateX(by: -crop.minX, yBy: -crop.minY)
            let path = StennyFigure.path()
            path.transform(using: t as AffineTransform)
            path.lineWidth = 1.5                                   // thicker than the logo's, to read at menu bar size
            path.lineCapStyle = .round
            path.lineJoinStyle = .round
            NSColor.black.setStroke()
            path.stroke()
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = "Stenny"
        return image
    }
}

/// The figure from stenny.hendrikboettcher.com: head, body, arms (one holding a pen), legs.
enum StennyFigure {
    static func path() -> NSBezierPath {
        let p = NSBezierPath(ovalIn: NSRect(x: 49, y: 11, width: 22, height: 22))
        for line in [[(60, 34), (60, 84)], [(60, 34), (50, 58), (46, 80)], [(60, 34), (72, 60), (90, 62)],
                     [(60, 84), (54, 114), (50, 144)], [(60, 84), (66, 114), (70, 144)]] {
            p.move(to: NSPoint(x: line[0].0, y: line[0].1))
            for pt in line.dropFirst() { p.line(to: NSPoint(x: pt.0, y: pt.1)) }
        }
        return p
    }
}
