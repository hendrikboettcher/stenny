"""Word files (.docx) for shared copies, without Word or textutil: Windows has no textutil, and a .docx is only a zip
of a few XML parts. share.py hands over the blocks it also turns into HTML (headings, paragraphs, quotes, bullets,
rules) and their inline HTML (bold, italics, timestamps), so the Word file says exactly what the PDF says.

It looks like the PDF (share.CSS): Arial, the same sizes in points, grey timestamps, quotes with a line on their
left. Headings are Word's own Heading 1–6 (the navigation pane and a table of contents find them), bullets a real
Word list, and the credit line links to stenny.eu.
"""

import re
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from xml.sax.saxutils import escape

FONT = "Arial"
INK, MUTED, FAINT, RULE = "1D1B18", "5F5A52", "8A847B", "DDD6CC"
# sizes in half-points, as share.CSS has them in px (= pt in the PDF): body 11, h1 16, h2 13, h3 11.5, h4–6 11
BODY, TS, QUOTE, CREDIT = 22, 17, 19, 17
HEADINGS = {1: (32, 0, 160), 2: (26, 280, 100), 3: (23, 220, 80), 4: (22, 180, 60), 5: (22, 180, 60), 6: (22, 180, 60)}
LINK = "https://stenny.eu"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class _Runs(HTMLParser):
    """share._inline's HTML (<strong>, <em>, <span class="ts">, <a>) → runs of (text, bold, italic, timestamp)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.runs: list[tuple[str, bool, bool, bool]] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.stack.append("ts" if tag == "span" and ("class", "ts") in attrs else tag)

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if data:
            s = self.stack
            self.runs.append((data, "strong" in s or "b" in s, "em" in s or "i" in s, "ts" in s))


def runs(inline_html: str) -> list[tuple[str, bool, bool, bool]]:
    parser = _Runs()
    parser.feed(inline_html)
    parser.close()
    return parser.runs


def _text(text: str) -> str:
    # XML 1.0 can't hold most control characters; Word refuses the whole file over one
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return f'<w:t xml:space="preserve">{escape(text)}</w:t>'


def _run(text: str, bold=False, italic=False, ts=False, size: int | None = None, color: str | None = None,
         style: str | None = None) -> str:
    props = []
    if style:
        props.append(f'<w:rStyle w:val="{style}"/>')
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    if ts:
        color, size = FAINT, TS
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    if size:
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f"<w:r>{rpr}{_text(text)}</w:r>"


def _para(inline_html: str, style: str | None = None, ppr: str = "", size: int | None = None,
          color: str | None = None) -> str:
    props = (f'<w:pStyle w:val="{style}"/>' if style else "") + ppr
    body = "".join(_run(t, b, i, ts, size=None if ts else size, color=None if ts else color)
                   for t, b, i, ts in runs(inline_html))
    return f"<w:p>{f'<w:pPr>{props}</w:pPr>' if props else ''}{body}</w:p>"


def document(blocks: list[tuple], inline, credit: bool) -> str:
    """word/document.xml for share.blocks(); `inline` is share._inline (Markdown inline → HTML)."""
    out = []
    for kind, *rest in blocks:
        if kind == "h":
            level, text = rest
            out.append(_para(inline(text), style=f"Heading{level}"))
        elif kind == "p":
            out.append(_para(inline(rest[0])))
        elif kind == "quote":
            out.extend(_para(inline(q), style="Quote") for q in rest[0])
        elif kind == "list":
            out.extend(_para(inline(item), style="ListBullet") for item in rest[0])
        elif kind == "hr":
            out.append('<w:p><w:pPr><w:pStyle w:val="Rule"/></w:pPr></w:p>')
    if credit:
        out.append('<w:p><w:pPr><w:pStyle w:val="Credit"/></w:pPr>'
                   f'{_run("Transcribed with Stenny · ")}'
                   f'<w:hyperlink r:id="rIdLink">{_run("stenny.eu", style="Hyperlink")}</w:hyperlink></w:p>')
    # A4 with 2.5 cm margins
    section = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1418" w:right="1418" w:bottom="1418" '
               'w:left="1418" w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{"".join(out)}{section}</w:body></w:document>')


def _style(style_id: str, name: str, ppr: str = "", rpr: str = "", based: str = "Normal", extra: str = "") -> str:
    return (f'<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{name}"/>'
            f'<w:basedOn w:val="{based}"/><w:next w:val="Normal"/>{extra}<w:qFormat/>'
            f"<w:pPr>{ppr}</w:pPr><w:rPr>{rpr}</w:rPr></w:style>")


def styles() -> str:
    fonts = f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}" w:cs="{FONT}"/>'
    parts = [
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        f'{fonts}<w:color w:val="{INK}"/><w:sz w:val="{BODY}"/><w:szCs w:val="{BODY}"/>'
        '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
        '<w:spacing w:before="0" w:after="120" w:line="324" w:lineRule="auto"/>'  # 6 pt after, line height 1.35
        '</w:pPr></w:pPrDefault></w:docDefaults>',
        # the defaults again in Normal: some readers (macOS's Quick Look and TextEdit) skip docDefaults
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:spacing w:before="0" w:after="120" w:line="324" w:lineRule="auto"/></w:pPr>'
        f'<w:rPr>{fonts}<w:color w:val="{INK}"/><w:sz w:val="{BODY}"/><w:szCs w:val="{BODY}"/></w:rPr></w:style>',
    ]
    for level, (size, before, after) in HEADINGS.items():
        parts.append(_style(f"Heading{level}", f"heading {level}",
                            ppr=f'<w:keepNext/><w:spacing w:before="{before}" w:after="{after}"/>'
                                f'<w:outlineLvl w:val="{level - 1}"/>',
                            rpr=f'<w:b/><w:bCs/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/>',
                            extra='<w:uiPriority w:val="9"/>'))
    parts += [
        _style("Quote", "Quote",
               ppr=f'<w:pBdr><w:left w:val="single" w:sz="12" w:space="8" w:color="{RULE}"/></w:pBdr>'
                   '<w:spacing w:after="80"/><w:ind w:left="170"/>',
               rpr=f'<w:color w:val="{MUTED}"/><w:sz w:val="{QUOTE}"/><w:szCs w:val="{QUOTE}"/>'),
        _style("ListBullet", "List Bullet",
               ppr='<w:numPr><w:numId w:val="1"/></w:numPr><w:spacing w:after="40"/><w:ind w:left="360" w:hanging="240"/>'),
        _style("Rule", "Rule",
               ppr=f'<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="{RULE}"/></w:pBdr>'
                   '<w:spacing w:before="120" w:after="240" w:line="120" w:lineRule="exact"/>'),
        _style("Credit", "Stenny Credit", ppr='<w:spacing w:before="360"/>',
               rpr=f'<w:color w:val="{FAINT}"/><w:sz w:val="{CREDIT}"/><w:szCs w:val="{CREDIT}"/>'),
        '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>'
        f'<w:rPr><w:color w:val="{FAINT}"/><w:u w:val="single"/></w:rPr></w:style>',
    ]
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<w:styles xmlns:w="{W}">{"".join(parts)}</w:styles>')


NUMBERING = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering xmlns:w="{W}">'
             '<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/>'
             '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/>'
             '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="360" w:hanging="240"/></w:pPr>'
             f'<w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}"/></w:rPr></w:lvl></w:abstractNum>'
             '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')

CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                 'wordprocessingml.document.main+xml"/>'
                 '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                 'wordprocessingml.styles+xml"/>'
                 '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                 'wordprocessingml.numbering+xml"/>'
                 '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.'
                 'core-properties+xml"/>'
                 '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                 'extended-properties+xml"/>'
                 '</Types>')

ROOT_RELS = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PKG}">'
             f'<Relationship Id="rId1" Type="{DOC}/officeDocument" Target="word/document.xml"/>'
             '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
             'core-properties" Target="docProps/core.xml"/>'
             f'<Relationship Id="rId3" Type="{DOC}/extended-properties" Target="docProps/app.xml"/>'
             '</Relationships>')

DOCUMENT_RELS = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PKG}">'
                 f'<Relationship Id="rIdStyles" Type="{DOC}/styles" Target="styles.xml"/>'
                 f'<Relationship Id="rIdNumbering" Type="{DOC}/numbering" Target="numbering.xml"/>'
                 f'<Relationship Id="rIdLink" Type="{DOC}/hyperlink" Target="{LINK}" TargetMode="External"/>'
                 '</Relationships>')

APP = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
       '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
       '<Application>Stenny</Application></Properties>')


def core(title: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{escape(title)}</dc:title>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
            "</cp:coreProperties>")


def write(path: Path, blocks: list[tuple], inline, title: str, credit: bool = True) -> None:
    """The .docx at `path`. `blocks` and `inline` come from share.py (share.blocks, share._inline)."""
    parts = {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": ROOT_RELS,
        "docProps/core.xml": core(title),
        "docProps/app.xml": APP,
        "word/_rels/document.xml.rels": DOCUMENT_RELS,
        "word/document.xml": document(blocks, inline, credit),
        "word/styles.xml": styles(),
        "word/numbering.xml": NUMBERING,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, xml in parts.items():
            z.writestr(name, xml.encode("utf-8"))
