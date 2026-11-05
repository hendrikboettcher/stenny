import AppKit

/// `stenny-agent --pdf <copy.html>`: the shared copy's HTML (`stenny share --format html`, the engine's
/// `make_copy`) laid out on pages of the Mac's default paper size by the text system and saved as a PDF next to
/// it; the HTML is removed. Prints "pdf <path>" or "error …". Ported from the Swift app's PDFExport
/// (app/Sources/Stenny/LibraryWindow.swift): no Python library makes paginated PDFs on a plain Mac.
struct PDFFailure: Error { let message: String }

@MainActor
func makePDF(from page: URL) -> Result<URL, PDFFailure> {
    defer { try? FileManager.default.removeItem(at: page) }
    let pdf = page.deletingPathExtension().appendingPathExtension("pdf")
    guard let data = try? Data(contentsOf: page),
          let text = NSAttributedString(html: data, baseURL: page.deletingLastPathComponent(), documentAttributes: nil)
    else { return .failure(PDFFailure(message: "The PDF couldn't be made: the copy couldn't be read.")) }
    let info = NSPrintInfo.shared.copy() as! NSPrintInfo
    (info.topMargin, info.bottomMargin, info.leftMargin, info.rightMargin) = (54, 54, 62, 62)
    info.horizontalPagination = .fit
    info.verticalPagination = .automatic
    info.isHorizontallyCentered = false
    info.isVerticallyCentered = false
    info.jobDisposition = .save
    info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = pdf
    let width = info.paperSize.width - info.leftMargin - info.rightMargin
    let view = NSTextView(frame: NSRect(x: 0, y: 0, width: width, height: 1))
    view.appearance = NSAppearance(named: .aqua)  // paper is white, also in dark mode
    view.textContainer?.lineFragmentPadding = 0
    view.textStorage?.setAttributedString(text)
    view.layoutManager?.ensureLayout(for: view.textContainer!)
    view.sizeToFit()
    try? FileManager.default.removeItem(at: pdf)
    let operation = NSPrintOperation(view: view, printInfo: info)
    operation.showsPrintPanel = false
    operation.showsProgressPanel = false
    guard operation.run(), FileManager.default.fileExists(atPath: pdf.path) else {
        return .failure(PDFFailure(message: "The PDF couldn't be made."))
    }
    return .success(pdf)
}
