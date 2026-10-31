// stenny-rec: records a microphone to a file as 16 kHz mono 16-bit PCM (s16le), appending.
//
// Replaces ffmpeg's avfoundation input for live mode: in tests on an M4 MacBook Air that input
// silently dropped ~1 s of audio every ~8 s (12 %), on both the built-in and the iPhone microphone.
// This uses AVAudioEngine (CoreAudio) directly and converts with AVAudioConverter.
//
//   stenny-rec --out <file.pcm> [--device "<name>"]     stops on SIGTERM/SIGINT
//   stenny-rec --list                                    prints input device names
import AVFoundation
import CoreAudio
import Foundation

setvbuf(stdout, nil, _IOLBF, 0)

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(("stenny-rec: error: " + message + "\n").data(using: .utf8)!)
    exit(1)
}

func log(_ message: String) {
    FileHandle.standardError.write(("stenny-rec: " + message + "\n").data(using: .utf8)!)
}

// MARK: Core Audio device lookup

func property<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector,
                 _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal, _ value: inout T) -> Bool {
    var address = AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    return AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value) == noErr
}

func allDevices() -> [AudioDeviceID] {
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices,
                                             mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr else { return [] }
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func name(of device: AudioDeviceID) -> String {
    var cfName: Unmanaged<CFString>?
    _ = property(device, kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal, &cfName)
    return (cfName?.takeRetainedValue() as String?) ?? "?"
}

func hasInput(_ device: AudioDeviceID) -> Bool {
    var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration,
                                             mScope: kAudioObjectPropertyScopeInput, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(device, &address, 0, nil, &size) == noErr, size > 0 else { return false }
    let list = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
    defer { list.deallocate() }
    guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, list) == noErr else { return false }
    let buffers = UnsafeMutableAudioBufferListPointer(list.assumingMemoryBound(to: AudioBufferList.self))
    return buffers.contains { $0.mNumberChannels > 0 }
}

let inputs = allDevices().filter(hasInput)

// MARK: Arguments

var outPath: String?
var deviceName: String?
var args = CommandLine.arguments.dropFirst()
while let arg = args.popFirst() {
    switch arg {
    case "--list":
        inputs.forEach { print(name(of: $0)) }
        exit(0)
    case "--out": outPath = args.popFirst()
    case "--device": deviceName = args.popFirst()
    default: fail("unknown argument \(arg)")
    }
}
guard let outPath else { fail("--out <file> is required") }

// MARK: Recording

let engine = AVAudioEngine()
let input = engine.inputNode
let outFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!

// Append, never replace: after a pause or reconnect the file already holds the lecture so far.
// (FileManager.createFile would truncate an existing file.)
if !FileManager.default.fileExists(atPath: outPath) {
    FileManager.default.createFile(atPath: outPath, contents: nil)
}
guard let out = FileHandle(forWritingAtPath: outPath) else { fail("cannot open \(outPath)") }
out.seekToEndOfFile()

let writeQueue = DispatchQueue(label: "stenny-rec.write")
var framesIn: Int64 = 0
var framesOut: Int64 = 0

func selectDevice() {
    guard let deviceName, deviceName != "default" else { return }
    guard let device = allDevices().filter(hasInput).first(where: { name(of: $0) == deviceName }) else {
        fail("no input device named \"\(deviceName)\" (available: \(allDevices().filter(hasInput).map(name(of:)).joined(separator: ", ")))")
    }
    var id = device
    let status = AudioUnitSetProperty(input.audioUnit!, kAudioOutputUnitProperty_CurrentDevice,
                                      kAudioUnitScope_Global, 0, &id, UInt32(MemoryLayout<AudioDeviceID>.size))
    guard status == noErr else { fail("could not select \"\(deviceName)\" (status \(status))") }
}

/// (Re)starts capture with the device's current format. Called at launch and after every
/// configuration change (selecting a device, sample-rate switch, Bluetooth/Continuity hiccup):
/// AVAudioEngine stops itself on such a change and has to be set up again.
func startCapture() throws {
    selectDevice()
    let inFormat = input.outputFormat(forBus: 0)
    guard inFormat.sampleRate > 0 else {
        throw NSError(domain: "stenny-rec", code: 1, userInfo: [NSLocalizedDescriptionKey:
            "the input delivers no audio format (microphone permission?)"])
    }
    guard let converter = AVAudioConverter(from: inFormat, to: outFormat) else {
        throw NSError(domain: "stenny-rec", code: 2, userInfo: [NSLocalizedDescriptionKey: "no converter for \(inFormat)"])
    }
    input.removeTap(onBus: 0)
    input.installTap(onBus: 0, bufferSize: 4096, format: inFormat) { buffer, _ in
        framesIn += Int64(buffer.frameLength)
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * 16000 / inFormat.sampleRate) + 64
        guard let converted = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: capacity) else { return }
        var consumed = false
        var error: NSError?
        converter.convert(to: converted, error: &error) { _, status in
            if consumed {
                status.pointee = .noDataNow
                return nil
            }
            consumed = true
            status.pointee = .haveData
            return buffer
        }
        if let error { log("conversion failed: \(error.localizedDescription)"); return }
        let n = Int(converted.frameLength)
        guard n > 0, let samples = converted.int16ChannelData?[0] else { return }
        let data = Data(bytes: samples, count: n * 2)
        framesOut += Int64(n)
        writeQueue.async {
            // A full disk: everything written so far is safe, so end here instead of crashing on write
            do { try out.write(contentsOf: data) } catch {
                log("writing failed (\(error.localizedDescription)); stopping")
                exit(4)
            }
        }
    }
    try engine.start()
    log("recording \(deviceName ?? "default input") at \(Int(inFormat.sampleRate)) Hz → \(outPath)")
}

func finish(_ code: Int32) -> Never {
    engine.stop()
    input.removeTap(onBus: 0)
    writeQueue.sync { try? out.synchronize(); try? out.close() }
    log("stopped: \(framesIn) frames in, \(framesOut) frames written")
    exit(code)
}

do {
    try startCapture()
} catch {
    fail("could not start recording: \(error.localizedDescription)")
}

// Stop cleanly on SIGTERM / SIGINT: stop the engine, flush pending writes, exit.
var signalSources: [DispatchSourceSignal] = []
for sig in [SIGTERM, SIGINT] {
    signal(sig, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    source.setEventHandler { finish(0) }
    source.resume()
    signalSources.append(source)
}

// A configuration change stops the engine: set it up again. Only give up (exit 3, so the caller
// reconnects) if the device is really gone or restarting keeps failing.
var recentRestarts: [Date] = []
NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { _ in
    recentRestarts = recentRestarts.filter { $0.timeIntervalSinceNow > -10 } + [Date()]
    if let deviceName, deviceName != "default",
       !allDevices().filter(hasInput).contains(where: { name(of: $0) == deviceName }) {
        log("input device \"\(deviceName)\" is gone")
        finish(3)
    }
    if recentRestarts.count > 5 {
        log("audio configuration keeps changing; giving up so the caller can reconnect")
        finish(3)
    }
    log("audio configuration changed; restarting capture")
    engine.stop()
    do { try startCapture() } catch {
        log("restart failed: \(error.localizedDescription)")
        finish(3)
    }
}

// If the process that started us dies we keep recording, so the lecture goes on while Stenny (`stenny recover`)
// takes over within seconds. If nobody does, stop after a while: the microphone must not stay on unnoticed.
let orphanLimit: TimeInterval = 300
var orphanedAt: Date?
let parentWatch = DispatchSource.makeTimerSource(queue: .main)
parentWatch.schedule(deadline: .now() + 5, repeating: 5)
parentWatch.setEventHandler {
    guard getppid() == 1 else { return }
    let since = orphanedAt ?? Date()
    orphanedAt = since
    if Date().timeIntervalSince(since) > orphanLimit {
        log("the recording's process is gone and nobody took over; stopping")
        finish(5)
    }
}
parentWatch.resume()

dispatchMain()
