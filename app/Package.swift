// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Stenny",
    platforms: [.macOS("14.4")],
    targets: [
        .executableTarget(name: "Stenny", path: "Sources/Stenny"),
        // native microphone recorder used by `stenny live` (bundled inside Stenny.app)
        .executableTarget(name: "stenny-rec", path: "Sources/stenny-rec"),
        // the new app's (desktop/) macOS helper: the global pause shortcut
        .executableTarget(name: "stenny-agent", path: "Sources/stenny-agent")
    ]
)
