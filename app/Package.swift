// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Stenny",
    platforms: [.macOS("14.4")],
    targets: [
        .executableTarget(name: "Stenny", path: "Sources/Stenny"),
        // native microphone recorder used by `stenny live` (bundled inside Stenny.app)
        .executableTarget(name: "stenny-rec", path: "Sources/stenny-rec"),
        // the new app's (desktop/) macOS helper: pause shortcut, calendar, open at login, PDF for Share…
        .executableTarget(name: "stenny-agent", path: "Sources/stenny-agent")
    ]
)
