#!/usr/bin/env swift
// Render a 1024x1024 PNG used as the source for ClaudeWatch's AppIcon.icns.
//
// Run:
//   swift mac/scripts/render-icon.swift [output.png]
//
// Default output:   mac/build/icon-source.png
//
// Design: rounded-square emerald→cyan gradient with a centered "🐜" glyph and
// a faint inner highlight, matching the menu-bar metaphor used elsewhere in
// the app. Pure Cocoa/AppKit so it has zero external dependencies.

import AppKit
import CoreGraphics
import CoreText
import Foundation

let size: CGFloat = 1024
let cornerRadius: CGFloat = 224     // ≈ Apple's "squircle" feel at 1024
let glyph = "🐜"

// ── Determine output path ───────────────────────────────────────────────────
let args = CommandLine.arguments
let outPath: String
if args.count >= 2 {
    outPath = args[1]
} else {
    let cwd = FileManager.default.currentDirectoryPath
    outPath = "\(cwd)/mac/build/icon-source.png"
}

let outURL = URL(fileURLWithPath: outPath)
try? FileManager.default.createDirectory(
    at: outURL.deletingLastPathComponent(),
    withIntermediateDirectories: true
)

// ── Draw into a bitmap ──────────────────────────────────────────────────────
guard let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil,
    pixelsWide: Int(size),
    pixelsHigh: Int(size),
    bitsPerSample: 8,
    samplesPerPixel: 4,
    hasAlpha: true,
    isPlanar: false,
    colorSpaceName: .deviceRGB,
    bytesPerRow: 0,
    bitsPerPixel: 0
) else {
    FileHandle.standardError.write("failed to create bitmap rep\n".data(using: .utf8)!)
    exit(1)
}
rep.size = NSSize(width: size, height: size)

NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
guard let ctx = NSGraphicsContext.current?.cgContext else {
    FileHandle.standardError.write("no CG context\n".data(using: .utf8)!)
    exit(1)
}

let rect = CGRect(x: 0, y: 0, width: size, height: size)

// Rounded-square clip → all drawing stays inside the squircle.
let path = CGPath(
    roundedRect: rect,
    cornerWidth: cornerRadius,
    cornerHeight: cornerRadius,
    transform: nil
)
ctx.saveGState()
ctx.addPath(path)
ctx.clip()

// Emerald → cyan diagonal gradient (top-left bright, bottom-right deeper).
let emerald = CGColor(red: 0.18, green: 0.78, blue: 0.55, alpha: 1.0)   // ~ tailwind emerald-400
let cyan    = CGColor(red: 0.07, green: 0.49, blue: 0.74, alpha: 1.0)   // ~ tailwind cyan-700
let gradient = CGGradient(
    colorsSpace: CGColorSpaceCreateDeviceRGB(),
    colors: [emerald, cyan] as CFArray,
    locations: [0.0, 1.0]
)!
ctx.drawLinearGradient(
    gradient,
    start: CGPoint(x: 0, y: size),
    end: CGPoint(x: size, y: 0),
    options: []
)

// Subtle top highlight for a touch of depth (white at ~12 % opacity).
let highlight = CGGradient(
    colorsSpace: CGColorSpaceCreateDeviceRGB(),
    colors: [
        CGColor(red: 1, green: 1, blue: 1, alpha: 0.18),
        CGColor(red: 1, green: 1, blue: 1, alpha: 0.0),
    ] as CFArray,
    locations: [0.0, 1.0]
)!
ctx.drawLinearGradient(
    highlight,
    start: CGPoint(x: size / 2, y: size),
    end: CGPoint(x: size / 2, y: size * 0.45),
    options: []
)

// Soft inner shadow on the bottom edge.
ctx.setBlendMode(.normal)
let darken = CGGradient(
    colorsSpace: CGColorSpaceCreateDeviceRGB(),
    colors: [
        CGColor(red: 0, green: 0, blue: 0, alpha: 0.0),
        CGColor(red: 0, green: 0, blue: 0, alpha: 0.18),
    ] as CFArray,
    locations: [0.6, 1.0]
)!
ctx.drawLinearGradient(
    darken,
    start: CGPoint(x: size / 2, y: size),
    end: CGPoint(x: size / 2, y: 0),
    options: []
)

ctx.restoreGState()

// ── Draw the glyph centered ─────────────────────────────────────────────────
// Emoji are color glyphs; AppleColorEmoji renders correctly via NSAttributedString.
let fontSize: CGFloat = 620
let font = NSFont(name: "Apple Color Emoji", size: fontSize)
    ?? NSFont.systemFont(ofSize: fontSize)
let para = NSMutableParagraphStyle()
para.alignment = .center
let attrs: [NSAttributedString.Key: Any] = [
    .font: font,
    .paragraphStyle: para,
    .shadow: {
        let s = NSShadow()
        s.shadowColor = NSColor.black.withAlphaComponent(0.28)
        s.shadowOffset = NSSize(width: 0, height: -8)
        s.shadowBlurRadius = 16
        return s
    }(),
]
let attributed = NSAttributedString(string: glyph, attributes: attrs)

// Measure & center. Apple Color Emoji's drawing rect doesn't sit on the
// baseline cleanly, so nudge by descender to keep the bug visually centered.
let textSize = attributed.size()
let textRect = NSRect(
    x: (size - textSize.width) / 2,
    y: (size - textSize.height) / 2 - fontSize * 0.06,
    width: textSize.width,
    height: textSize.height
)
attributed.draw(in: textRect)

// Subtle inner stroke right inside the corner to crisp the silhouette.
ctx.saveGState()
ctx.addPath(path)
ctx.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.10))
ctx.setLineWidth(6)
ctx.strokePath()
ctx.restoreGState()

NSGraphicsContext.restoreGraphicsState()

// ── Write PNG ───────────────────────────────────────────────────────────────
guard let data = rep.representation(using: .png, properties: [:]) else {
    FileHandle.standardError.write("failed to serialize PNG\n".data(using: .utf8)!)
    exit(1)
}
do {
    try data.write(to: outURL)
    print("wrote \(outURL.path)  (\(data.count) bytes)")
} catch {
    FileHandle.standardError.write("write failed: \(error)\n".data(using: .utf8)!)
    exit(1)
}
