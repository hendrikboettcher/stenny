import AppKit
import CoreText
import SwiftUI

// The look of stenny.eu (warm paper, Fraunces, hairlines, pill buttons) for Stenny's own windows.

enum Palette {
    static let bg = color(0xfbfaf7, dark: 0x161513)
    static let ink = color(0x1d1b18, dark: 0xece7df)
    static let muted = color(0x8a847b, dark: 0x8f887e)
    static let line = color(0xddd6cc, dark: 0x3a3631)
    static let mark = color(0xe9dfd5, dark: 0x3a332c)   // the beige highlighter
    static let bar = color(0xf3efe9, dark: 0x23211e)
    static let warn = color(0xa86a3d, dark: 0xd39a6c)
    static let rec = color(0xc0392b, dark: 0xe0675a)

    static let bgNSColor = nsColor(0xfbfaf7, dark: 0x161513)

    private static func nsColor(_ light: UInt32, dark: UInt32) -> NSColor {
        NSColor(name: nil) { appearance in
            let hex = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: CGFloat(hex >> 16 & 0xff) / 255, green: CGFloat(hex >> 8 & 0xff) / 255,
                           blue: CGFloat(hex & 0xff) / 255, alpha: 1)
        }
    }

    private static func color(_ light: UInt32, dark: UInt32) -> Color { Color(nsColor: nsColor(light, dark: dark)) }
}

enum Typeface {
    /// Fraunces ships inside Stenny.app (Resources/Fraunces.ttf, OFL); without it, the system's serif.
    private static let fraunces: Bool = {
        guard let url = Bundle.main.url(forResource: "Fraunces", withExtension: "ttf") else { return false }
        return CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
    }()

    /// Fraunces as on the website: soft and a little wonky, 360 for text, 440–480 for headings.
    static func serif(_ size: CGFloat, weight: CGFloat = 360) -> Font {
        guard fraunces else { return .system(size: size, design: .serif) }
        let axes: [NSNumber: CGFloat] = [tag("wght"): weight, tag("opsz"): min(max(size, 9), 144),
                                         tag("SOFT"): 100, tag("WONK"): 1]
        let descriptor = CTFontDescriptorCreateWithAttributes([kCTFontFamilyNameAttribute: "Fraunces",
                                                               kCTFontVariationAttribute: axes] as CFDictionary)
        return Font(CTFontCreateWithFontDescriptor(descriptor, size, nil))
    }

    /// Small interface text (the website uses Inter there; the system font is its stand-in).
    static func sans(_ size: CGFloat, weight: Font.Weight = .regular) -> Font { .system(size: size, weight: weight) }

    private static func tag(_ name: String) -> NSNumber {
        NSNumber(value: name.utf8.reduce(UInt32(0)) { $0 << 8 | UInt32($1) })
    }
}

/// The logo: the website's stick figure (`StennyFigure`), cropped to the figure, in ink.
/// Its stroke is a little heavier than the website's so it holds up next to the name at this size.
struct StennyLogo: View {
    var height: CGFloat = 30
    private static let crop = CGRect(x: 36, y: 2, width: 64, height: 152)   // as in MenuBarImage.figure

    var body: some View {
        let scale = height / Self.crop.height
        Path(StennyFigure.path().cgPath)
            .applying(CGAffineTransform(scaleX: scale, y: scale).translatedBy(x: -Self.crop.minX, y: -Self.crop.minY))
            .stroke(Palette.ink, style: StrokeStyle(lineWidth: 9 * scale, lineCap: .round, lineJoin: .round))
            .frame(width: Self.crop.width * scale, height: height)
            .accessibilityLabel("Stenny")
    }
}

/// Small uppercase label above a group, like the website's `.kicker`.
struct Kicker: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text.uppercased()).font(Typeface.sans(10.5, weight: .medium)).tracking(0.9).foregroundStyle(Palette.muted)
    }
}

/// The website's `.btn`: a pill with a hairline border, or filled with ink for the main action.
struct PillButtonStyle: ButtonStyle {
    var primary = false
    var small = false
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Typeface.sans(small ? 12 : 13, weight: .medium))
            .padding(.horizontal, small ? 11 : 15)
            .padding(.vertical, small ? 4 : 7)
            .foregroundStyle(primary ? Palette.bg : Palette.ink)
            .background(Capsule().fill(primary ? Palette.ink : Color.clear))
            .overlay(Capsule().strokeBorder(primary ? Palette.ink : Palette.line))
            .contentShape(Capsule())
            .opacity(!isEnabled ? 0.5 : configuration.isPressed ? 0.82 : 1)
            .offset(y: configuration.isPressed ? 1 : 0)
    }
}

/// Quiet text buttons (sidebar footer, "Lecturer agreed"): muted, ink on hover.
struct FooterButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        Hover { hovering in
            configuration.label
                .foregroundStyle(hovering || configuration.isPressed ? Palette.ink : Palette.muted)
                .background(hovering ? Palette.mark.opacity(0.6) : .clear, in: RoundedRectangle(cornerRadius: 8))
        }
    }

    private struct Hover<Content: View>: View {
        @ViewBuilder let content: (Bool) -> Content
        @State private var hovering = false
        var body: some View { content(hovering).onHover { hovering = $0 } }
    }
}

/// A 1 px line between areas.
struct Hairline: View {
    var vertical = false
    var body: some View {
        Rectangle().fill(Palette.line).frame(width: vertical ? 1 : nil, height: vertical ? nil : 1)
    }
}
