// Subject or person mask for a still image with Apple Vision - built into macOS 14+, no model to download.
// usage: swift subject_mask.swift <in.png|jpg> <out_mask.png> [subject|person]
//   subject: every salient foreground instance (a turtle, a diver, a boat) - VNGenerateForegroundInstanceMaskRequest
//   person:  people only, finer hair edges - VNGeneratePersonSegmentationRequest (accurate)
// The mask is 8-bit grey at the input's size: white = subject. Used to put cover type *behind* the subject.
import Foundation
import Vision
import CoreImage
import ImageIO
import UniformTypeIdentifiers

let args = CommandLine.arguments
guard args.count >= 3 else { print("usage: subject_mask <in> <out> [subject|person]"); exit(1) }
let input = URL(fileURLWithPath: args[1]), output = URL(fileURLWithPath: args[2])
let mode = args.count > 3 ? args[3] : "subject"
guard let source = CGImageSourceCreateWithURL(input as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { print("cannot read \(input.path)"); exit(1) }

let handler = VNImageRequestHandler(cgImage: image, options: [:])
var buffer: CVPixelBuffer
do {
    if mode == "person" {
        let request = VNGeneratePersonSegmentationRequest()
        request.qualityLevel = .accurate
        request.outputPixelFormat = kCVPixelFormatType_OneComponent8
        try handler.perform([request])
        guard let result = request.results?.first else { print("no person found"); exit(2) }
        buffer = result.pixelBuffer
    } else {
        let request = VNGenerateForegroundInstanceMaskRequest()
        try handler.perform([request])
        guard let result = request.results?.first else { print("no subject found"); exit(2) }
        buffer = try result.generateScaledMaskForImage(forInstances: result.allInstances, from: handler)
    }
} catch { print("vision error: \(error)"); exit(3) }

var mask = CIImage(cvPixelBuffer: buffer)
mask = mask.transformed(by: CGAffineTransform(scaleX: CGFloat(image.width) / mask.extent.width,
                                              y: CGFloat(image.height) / mask.extent.height))
let context = CIContext()
guard let cg = context.createCGImage(mask, from: CGRect(x: 0, y: 0, width: image.width, height: image.height),
                                     format: .L8, colorSpace: CGColorSpaceCreateDeviceGray()),
      let destination = CGImageDestinationCreateWithURL(output as CFURL, UTType.png.identifier as CFString, 1, nil)
else { print("cannot write \(output.path)"); exit(4) }
CGImageDestinationAddImage(destination, cg, nil)
CGImageDestinationFinalize(destination)
print("mask \(image.width)x\(image.height) -> \(output.path)")
