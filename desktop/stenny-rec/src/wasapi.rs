//! Capture through WASAPI, Windows' own audio API, in shared mode and event-driven: Windows signals each packet
//! (about every 10 ms), this thread takes it out of a one-second buffer and hands it to a writer thread, so neither
//! a busy engine nor a slow disk makes the device drop audio. The stream asks Windows for 16 kHz mono 16-bit
//! directly (AUTOCONVERTPCM, Windows' own resampler, like AVAudioConverter on the Mac); a device that refuses gets
//! its own format and src/convert.rs.

use std::collections::VecDeque;
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::mpsc;
use std::sync::{Arc, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use windows::core::{w, BOOL, GUID, HRESULT, PCWSTR, PWSTR};
use windows::Win32::Devices::FunctionDiscovery::PKEY_Device_FriendlyName;
use windows::Win32::Foundation::{CloseHandle, E_ACCESSDENIED, HANDLE};
use windows::Win32::Media::Audio::*;
use windows::Win32::Storage::FileSystem::{GetFileType, FILE_TYPE_PIPE};
use windows::Win32::System::Com::*;
use windows::Win32::System::Console::{GetStdHandle, SetConsoleCtrlHandler, STD_INPUT_HANDLE};
use windows::Win32::System::Threading::{AvSetMmThreadCharacteristicsW, CreateEventW, WaitForSingleObject};

use crate::args::Command;
use crate::control::{self, Control};
use crate::convert::{SampleFormat, Converter, OUT_RATE, WAVE_FORMAT_EXTENSIBLE};
use crate::devices::{self, Device};
use crate::{fail, log};

/// WASAPI's buffer: one second. The loop empties it every ~10 ms, so a stall of almost a second loses nothing.
const BUFFER_HNS: i64 = 10_000_000;
const KSDATAFORMAT_SUBTYPE_IEEE_FLOAT: GUID = GUID::from_u128(0x00000003_0000_0010_8000_00aa00389b71);
/// How often a recording of the default input checks whether the default changed.
const DEFAULT_CHECK: Duration = Duration::from_secs(2);

pub fn run(command: Command) {
    unsafe {
        if let Err(e) = CoInitializeEx(None, COINIT_MULTITHREADED).ok() {
            fail(&format!("could not start COM ({e})"));
        }
        let enumerator: IMMDeviceEnumerator = match CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL) {
            Ok(e) => e,
            Err(e) => fail(&format!("Windows' audio devices aren't available ({e})")),
        };
        match command {
            Command::List => {
                for d in endpoints(&enumerator) {
                    println!("{}", d.name);
                }
            }
            Command::ListJson => println!("{}", devices::to_json(&describe(&enumerator))),
            Command::Record { out, device } => record(&enumerator, &out, device.as_deref()),
        }
    }
}

// MARK: devices

struct Endpoint {
    device: IMMDevice,
    id: String,
    name: String,
}

unsafe fn take_string(p: PWSTR) -> String {
    let s = p.to_string().unwrap_or_default();
    CoTaskMemFree(Some(p.0 as *const _));
    s
}

unsafe fn name_of(device: &IMMDevice) -> String {
    device
        .OpenPropertyStore(STGM_READ)
        .and_then(|store| store.GetValue(&PKEY_Device_FriendlyName))
        .map(|v| v.to_string())
        .unwrap_or_else(|_| "?".into())
}

unsafe fn form_factor(device: &IMMDevice) -> Option<u32> {
    let value = device.OpenPropertyStore(STGM_READ).ok()?.GetValue(&PKEY_AudioEndpoint_FormFactor).ok()?;
    u32::try_from(&value).ok()
}

/// The id of the hardware behind the endpoint (`\\?\HDAUDIO#…`, `\\?\USB#…`), through its topology.
unsafe fn topology_id(device: &IMMDevice) -> Option<String> {
    let topology: IDeviceTopology = device.Activate(CLSCTX_ALL, None).ok()?;
    let connector = topology.GetConnector(0).ok()?;
    Some(take_string(connector.GetDeviceIdConnectedTo().ok()?))
}

/// Active microphones (and other inputs), in Windows' order.
unsafe fn endpoints(enumerator: &IMMDeviceEnumerator) -> Vec<Endpoint> {
    let Ok(collection) = enumerator.EnumAudioEndpoints(eCapture, DEVICE_STATE_ACTIVE) else { return vec![] };
    let count = collection.GetCount().unwrap_or(0);
    (0..count)
        .filter_map(|i| collection.Item(i).ok())
        .filter_map(|device| {
            let id = take_string(device.GetId().ok()?);
            let name = name_of(&device);
            Some(Endpoint { device, id, name })
        })
        .collect()
}

unsafe fn default_id(enumerator: &IMMDeviceEnumerator) -> Option<String> {
    let device = enumerator.GetDefaultAudioEndpoint(eCapture, eConsole).ok()?;
    Some(take_string(device.GetId().ok()?))
}

unsafe fn describe(enumerator: &IMMDeviceEnumerator) -> Vec<Device> {
    let default = default_id(enumerator);
    endpoints(enumerator)
        .into_iter()
        .map(|e| {
            let bus = topology_id(&e.device).map(|id| devices::bus(&id)).unwrap_or(devices::Bus::Unknown);
            Device {
                default: default.as_deref() == Some(e.id.as_str()),
                builtin: devices::is_builtin(&bus, form_factor(&e.device)),
                r#virtual: bus == devices::Bus::Virtual,
                name: e.name,
            }
        })
        .collect()
}

/// The device to record: the one named, or the system default.
unsafe fn find(enumerator: &IMMDeviceEnumerator, name: Option<&str>) -> Result<Endpoint, String> {
    match name {
        Some(name) => {
            let all = endpoints(enumerator);
            let names: Vec<String> = all.iter().map(|e| e.name.clone()).collect();
            all.into_iter().find(|e| e.name == name).ok_or_else(|| devices::not_found(name, &names))
        }
        None => {
            let device = enumerator.GetDefaultAudioEndpoint(eCapture, eConsole).map_err(|_| "there is no input device".to_string())?;
            let id = take_string(device.GetId().map_err(|e| e.to_string())?);
            let name = name_of(&device);
            Ok(Endpoint { device, id, name })
        }
    }
}

// MARK: one capture stream

struct Stream {
    client: IAudioClient,
    capture: IAudioCaptureClient,
    event: HANDLE,
    block_align: usize,
    converter: Option<Converter>, // None: Windows delivers 16 kHz mono s16le already
    rate: u32,                    // the device's own rate, for the log
}

impl Drop for Stream {
    fn drop(&mut self) {
        unsafe {
            let _ = self.client.Stop();
            let _ = CloseHandle(self.event);
        }
    }
}

fn explain(e: &windows::core::Error) -> String {
    if e.code() == E_ACCESSDENIED {
        "microphone access is off (Settings ▸ Privacy & security ▸ Microphone ▸ Let desktop apps access your microphone)".into()
    } else {
        e.to_string()
    }
}

unsafe fn open(device: &IMMDevice) -> Result<Stream, String> {
    let client: IAudioClient = device.Activate(CLSCTX_ALL, None).map_err(|e| explain(&e))?;
    let mix = client.GetMixFormat().map_err(|e| explain(&e))?;
    let rate = std::ptr::read_unaligned(mix).nSamplesPerSec;
    CoTaskMemFree(Some(mix as *const _));

    let want = WAVEFORMATEX {
        wFormatTag: WAVE_FORMAT_PCM as u16,
        nChannels: 1,
        nSamplesPerSec: OUT_RATE,
        nAvgBytesPerSec: OUT_RATE * 2,
        nBlockAlign: 2,
        wBitsPerSample: 16,
        cbSize: 0,
    };
    let flags = AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY;
    let (client, block_align, converter) = match client.Initialize(AUDCLNT_SHAREMODE_SHARED, flags, BUFFER_HNS, 0, &want, None) {
        Ok(()) => (client, 2, None),
        Err(converted) => {
            // a fresh client: one whose Initialize failed can't be initialized again
            let client: IAudioClient = device.Activate(CLSCTX_ALL, None).map_err(|e| explain(&e))?;
            let mix = client.GetMixFormat().map_err(|e| explain(&e))?;
            let format = std::ptr::read_unaligned(mix);
            let (tag, bits, channels, block_align) = (format.wFormatTag, format.wBitsPerSample, format.nChannels, format.nBlockAlign);
            let float = tag == WAVE_FORMAT_EXTENSIBLE && format.cbSize >= 22 && {
                let sub_format: GUID = std::ptr::read_unaligned(mix as *const WAVEFORMATEXTENSIBLE).SubFormat;
                sub_format == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT
            };
            let result = client.Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_EVENTCALLBACK, BUFFER_HNS, 0, mix, None);
            CoTaskMemFree(Some(mix as *const _));
            result.map_err(|e| format!("{} (and as 16 kHz: {})", explain(&e), explain(&converted)))?;
            let sample = SampleFormat::from_wave(tag, bits, float)
                .ok_or_else(|| format!("the device delivers a format Stenny can't read (tag {tag:#x}, {bits} bits)"))?;
            (client, block_align as usize, Some(Converter::new(rate, channels, sample)))
        }
    };
    let event = CreateEventW(None, false, false, PCWSTR::null()).map_err(|e| e.to_string())?;
    let setup = (|| -> windows::core::Result<IAudioCaptureClient> {
        client.SetEventHandle(event)?;
        let capture: IAudioCaptureClient = client.GetService()?;
        client.Start()?;
        Ok(capture)
    })();
    match setup {
        Ok(capture) => Ok(Stream { client, capture, event, block_align, converter, rate }),
        Err(e) => {
            let _ = CloseHandle(event);
            Err(explain(&e))
        }
    }
}

// MARK: recording

enum End {
    Stop,
    Orphaned,
    DefaultChanged,
    Failed(windows::core::Error),
}

struct Recording {
    control: Arc<Control>,
    writer: Option<mpsc::Sender<Vec<u8>>>,
    writer_thread: Option<thread::JoinHandle<()>>,
    frames_in: u64,
    frames_out: u64,
}

impl Recording {
    fn write(&mut self, samples: &[i16]) {
        if samples.is_empty() {
            return;
        }
        self.frames_out += samples.len() as u64;
        let bytes: Vec<u8> = samples.iter().flat_map(|s| s.to_le_bytes()).collect();
        if let Some(w) = &self.writer {
            let _ = w.send(bytes);
        }
    }

    /// Takes every packet waiting in WASAPI's buffer.
    unsafe fn drain(&mut self, stream: &mut Stream, discontinuity_ok: &mut bool) -> windows::core::Result<()> {
        let mut out: Vec<i16> = Vec::new();
        loop {
            if stream.capture.GetNextPacketSize()? == 0 {
                return Ok(());
            }
            let mut data: *mut u8 = std::ptr::null_mut();
            let mut frames = 0u32;
            let mut flags = 0u32;
            stream.capture.GetBuffer(&mut data, &mut frames, &mut flags, None, None)?;
            let bytes = frames as usize * stream.block_align;
            let silent = flags & AUDCLNT_BUFFERFLAGS_SILENT.0 as u32 != 0;
            if flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY.0 as u32 != 0 && *discontinuity_ok {
                // the device (or Windows) lost audio before it reached us: a warning `stenny check` counts
                log("audio discontinuity: the device dropped audio");
            }
            *discontinuity_ok = true; // the first packet of a stream always says so
            let packet: Vec<u8> = if silent || data.is_null() { vec![0; bytes] } else { std::slice::from_raw_parts(data, bytes).to_vec() };
            stream.capture.ReleaseBuffer(frames)?;
            self.frames_in += frames as u64;
            out.clear();
            match &mut stream.converter {
                Some(c) => c.process(&packet, &mut out),
                None => out.extend(packet.chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]]))),
            }
            self.write(&out);
        }
    }

    unsafe fn pump(&mut self, stream: &mut Stream, enumerator: &IMMDeviceEnumerator, follow_default: Option<&str>) -> End {
        let mut discontinuity_ok = false;
        let mut checked = Instant::now();
        loop {
            WaitForSingleObject(stream.event, 100);
            if let Err(e) = self.drain(stream, &mut discontinuity_ok) {
                return End::Failed(e);
            }
            if self.control.stop_requested() {
                return End::Stop;
            }
            if self.control.orphan_expired(Instant::now()) {
                return End::Orphaned;
            }
            if let Some(id) = follow_default {
                if checked.elapsed() >= DEFAULT_CHECK {
                    checked = Instant::now();
                    if default_id(enumerator).is_some_and(|now| now != id) {
                        return End::DefaultChanged;
                    }
                }
            }
        }
    }

    /// Stops capture, writes what's left and exits (the Swift recorder's finish()).
    fn finish(&mut self, stream: Option<Stream>, code: i32) -> ! {
        let mut stream = stream;
        if let Some(s) = stream.as_mut() {
            if let Some(c) = s.converter.as_mut() {
                let mut rest = Vec::new();
                c.flush(&mut rest);
                self.write(&rest);
            }
        }
        drop(stream);
        drop(self.writer.take());
        if let Some(t) = self.writer_thread.take() {
            let _ = t.join();
        }
        log(&format!("stopped: {} frames in, {} frames written", self.frames_in, self.frames_out));
        std::process::exit(code)
    }
}

static CONTROL: OnceLock<Arc<Control>> = OnceLock::new();

unsafe extern "system" fn on_console_event(kind: u32) -> BOOL {
    // Ctrl+C, Ctrl+Break: stop like on `stop`
    if let Some(c) = CONTROL.get() {
        c.request_stop();
    }
    // closing the console, logging off, shutting down: Windows ends the process as soon as this returns, so wait
    // here while the recording loop writes what's left and exits (Windows gives it 5 s)
    if kind >= 2 {
        thread::sleep(Duration::from_secs(5));
    }
    BOOL(1)
}

unsafe fn record(enumerator: &IMMDeviceEnumerator, path: &str, device: Option<&str>) {
    let control = Arc::new(Control::default());
    let _ = CONTROL.set(control.clone());
    let _ = SetConsoleCtrlHandler(Some(on_console_event), true);
    // this thread takes the audio out of WASAPI's buffer: Windows' scheduler for audio (MMCSS) keeps it on time
    // while the engine transcribes (if the service isn't there, normal priority with a 1 s buffer still does)
    let mut task = 0u32;
    let _ = AvSetMmThreadCharacteristicsW(w!("Audio"), &mut task);
    let stdin_is_pipe = GetStdHandle(STD_INPUT_HANDLE).is_ok_and(|h| !h.is_invalid() && GetFileType(h) == FILE_TYPE_PIPE);
    {
        let control = control.clone();
        thread::spawn(move || control::watch_input(std::io::stdin().lock(), &control, stdin_is_pipe));
    }

    // Append, never replace: after a pause or reconnect the file already holds the lecture so far.
    let mut file = match OpenOptions::new().create(true).append(true).open(path) {
        Ok(f) => f,
        Err(e) => fail(&format!("cannot open {path} ({e})")),
    };
    let (sender, receiver) = mpsc::channel::<Vec<u8>>();
    let writer_thread = thread::spawn(move || {
        for bytes in receiver {
            // A full disk: everything written so far is safe, so end here instead of crashing on write
            if let Err(e) = file.write_all(&bytes) {
                log(&format!("writing failed ({e}); stopping"));
                std::process::exit(4);
            }
        }
        let _ = file.sync_all();
    });
    let mut rec = Recording { control, writer: Some(sender), writer_thread: Some(writer_thread), frames_in: 0, frames_out: 0 };

    let start = || -> Result<(Stream, String), String> {
        let endpoint = find(enumerator, device)?;
        let stream = open(&endpoint.device)?;
        let label = match device {
            Some(name) => name.to_string(),
            None => format!("default input ({})", endpoint.name),
        };
        log(&format!("recording {label} at {} Hz → {path}", stream.rate));
        Ok((stream, endpoint.id))
    };

    let (mut stream, mut id) = match start() {
        Ok(s) => s,
        Err(e) => fail(&format!("could not start recording: {e}")),
    };
    // A device change ends the stream: set it up again. Only give up (exit 3, so the engine reconnects) if the
    // device is really gone or restarting keeps failing.
    let mut restarts: VecDeque<Instant> = VecDeque::new();
    loop {
        let follow = if device.is_none() { Some(id.as_str()) } else { None };
        let end = rec.pump(&mut stream, enumerator, follow);
        match end {
            End::Stop => rec.finish(Some(stream), 0),
            End::Orphaned => {
                log("the recording's process is gone and nobody took over; stopping");
                rec.finish(Some(stream), 5)
            }
            End::DefaultChanged => log("the default input changed; restarting capture"),
            End::Failed(e) => {
                if let Some(name) = device {
                    if !endpoints(enumerator).iter().any(|e| e.name == name) {
                        log(&format!("input device \"{name}\" is gone"));
                        rec.finish(Some(stream), 3);
                    }
                }
                let code: HRESULT = e.code();
                if code == AUDCLNT_E_DEVICE_INVALIDATED || code == AUDCLNT_E_RESOURCES_INVALIDATED {
                    log("audio configuration changed; restarting capture");
                } else {
                    log(&format!("capture failed ({e}); restarting capture"));
                }
            }
        }
        let now = Instant::now();
        restarts.retain(|t| now.duration_since(*t) < Duration::from_secs(10));
        restarts.push_back(now);
        if restarts.len() > 5 {
            log("audio configuration keeps changing; giving up so the caller can reconnect");
            rec.finish(Some(stream), 3);
        }
        // keep what the old stream's filter still holds, then let it go before opening the device again
        if let Some(c) = stream.converter.as_mut() {
            let mut rest = Vec::new();
            c.flush(&mut rest);
            rec.write(&rest);
        }
        drop(stream);
        match start() {
            Ok((s, new_id)) => {
                stream = s;
                id = new_id;
            }
            Err(e) => {
                log(&format!("restart failed: {e}"));
                rec.finish(None, 3);
            }
        }
    }
}
