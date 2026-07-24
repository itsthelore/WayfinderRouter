// swift-tools-version: 6.0

import PackageDescription

let package = Package(
  name: "WayfinderIOSCompileCheck",
  platforms: [
    .iOS(.v18)
  ],
  products: [
    .library(name: "WayfinderIOS", targets: ["WayfinderIOS"])
  ],
  dependencies: [
    .package(path: "../../apple/Packages/WayfinderRoutingBridge")
  ],
  targets: [
    .target(
      name: "WayfinderIOS",
      dependencies: [
        .product(
          name: "WayfinderRoutingBridge",
          package: "WayfinderRoutingBridge"
        )
      ],
      path: "WayfinderIOS",
      exclude: ["WayfinderIOSApp.swift"]
    ),
    .testTarget(
      name: "WayfinderIOSTests",
      dependencies: ["WayfinderIOS"],
      path: "WayfinderIOSTests"
    ),
  ]
)
