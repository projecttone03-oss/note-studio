// 参考書籍PDFを読むための小さなツール（macOS標準のPDFKitを使用、追加インストール不要）
//
// ビルド（初回のみ）: swiftc -O tools/pdftool.swift -o tools/pdftool
// 使い方:
//   tools/pdftool info   <pdf>                      ページ数・暗号化・文字抽出の可否
//   tools/pdftool text   <pdf> <from> <to>          指定ページの文字（1始まり、PDFのページ番号）
//   tools/pdftool find   <pdf> <キーワード> [...]     キーワードを含むページ番号と前後の文
//   tools/pdftool render <pdf> <page> <out.png> [幅px]  ページを画像にする（文字化けの確認・印刷ページ番号の確認用）
//
// 縦書きのページは1文字ずつ改行されて抽出されることがあるため、find は空白と改行を除いてから探す。
import AppKit
import Foundation
import PDFKit

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count >= 3 else { fail("usage: pdftool info|text|find|render <pdf> ...") }
guard let doc = PDFDocument(url: URL(fileURLWithPath: args[2])) else { fail("PDFを開けません: \(args[2])") }

func pageText(_ i: Int) -> String { doc.page(at: i)?.string ?? "" }
func squash(_ s: String) -> String { s.components(separatedBy: .whitespacesAndNewlines).joined() }

switch args[1] {
case "info":
    let n = doc.pageCount
    var textPages = 0
    let samples = Array(stride(from: 0, to: n, by: max(1, n / 10)))
    for i in samples where squash(pageText(i)).count > 30 { textPages += 1 }
    print("pages=\(n) encrypted=\(doc.isEncrypted) locked=\(doc.isLocked) text_pages=\(textPages)/\(samples.count)")

case "text":
    guard args.count >= 5, let from = Int(args[3]), let to = Int(args[4]) else { fail("text <pdf> <from> <to>") }
    for i in max(0, from - 1)..<min(to, doc.pageCount) {
        print("=== PDF p.\(i + 1) ===")
        print(pageText(i))
    }

case "find":
    guard args.count >= 4 else { fail("find <pdf> <キーワード>...") }
    let keywords = Array(args[3...])
    for i in 0..<doc.pageCount {
        let s = squash(pageText(i))
        for kw in keywords {
            guard let r = s.range(of: kw) else { continue }
            let lo = s.index(r.lowerBound, offsetBy: -40, limitedBy: s.startIndex) ?? s.startIndex
            let hi = s.index(r.upperBound, offsetBy: 60, limitedBy: s.endIndex) ?? s.endIndex
            print("PDF p.\(i + 1) [\(kw)] …\(s[lo..<hi])…")
        }
    }

case "render":
    guard args.count >= 5, let p = Int(args[3]), let page = doc.page(at: p - 1) else { fail("render <pdf> <page> <out.png>") }
    let width = args.count > 5 ? CGFloat(Double(args[5]) ?? 900) : 900
    let box = page.bounds(for: .mediaBox)
    let img = page.thumbnail(of: NSSize(width: width, height: width * box.height / box.width), for: .mediaBox)
    guard let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else { fail("画像にできません") }
    try png.write(to: URL(fileURLWithPath: args[4]))
    print("wrote \(args[4])")

default:
    fail("unknown command: \(args[1])")
}
