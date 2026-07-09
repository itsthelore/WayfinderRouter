// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "WayfinderMac",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "WayfinderMac", targets: ["WayfinderMacApp"])
    ],
    targets: [
        .target(
            name: "WayfinderMacCore",
            path: "Sources/WayfinderMac"
        ),
        .executableTarget(
            name: "WayfinderMacApp",
            dependencies: ["WayfinderMacCore"],
            path: "Sources/WayfinderMacApp"
        ),
        .testTarget(
            name: "WayfinderMacTests",
            dependencies: ["WayfinderMacCore"],
            path: "Tests/WayfinderMacTests"
        ),
    ]
)
