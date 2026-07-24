// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "WayfinderRoutingBridge",
    platforms: [
        .iOS(.v18),
        .macOS(.v14),
    ],
    products: [
        .library(
            name: "WayfinderRoutingBridge",
            targets: ["WayfinderRoutingBridge"]
        ),
    ],
    targets: [
        .binaryTarget(
            name: "WayfinderRoutingFFI",
            path: "Artifacts/WayfinderRoutingFFI.xcframework"
        ),
        .target(
            name: "WayfinderRoutingBridge",
            dependencies: ["WayfinderRoutingFFI"],
            path: "Sources/WayfinderRoutingBridge"
        ),
        .testTarget(
            name: "WayfinderRoutingBridgeTests",
            dependencies: ["WayfinderRoutingBridge"]
        ),
    ]
)
