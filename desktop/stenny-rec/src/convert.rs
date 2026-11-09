//! What Stenny records: 16 kHz mono 16-bit PCM (s16le), like the Swift recorder writes.
//!
//! Normally Windows converts for us (the stream asks for exactly that format, AUTOCONVERTPCM). This is the way
//! for a device whose driver refuses that: take the device's own format, mix the channels down to one and
//! resample with a windowed-sinc low-pass filter.

pub const OUT_RATE: u32 = 16_000;

/// How one sample is stored in what the device delivers.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum SampleFormat {
    F32,
    I16,
    I24,
    I32,
}

pub const WAVE_FORMAT_PCM: u16 = 1;
pub const WAVE_FORMAT_IEEE_FLOAT: u16 = 3;
pub const WAVE_FORMAT_EXTENSIBLE: u16 = 0xFFFE;

impl SampleFormat {
    /// From a WAVEFORMATEX: its tag, bits per sample (the container) and, for WAVE_FORMAT_EXTENSIBLE, whether its
    /// SubFormat is IEEE float.
    pub fn from_wave(tag: u16, bits: u16, extensible_float: bool) -> Option<Self> {
        let float = tag == WAVE_FORMAT_IEEE_FLOAT || (tag == WAVE_FORMAT_EXTENSIBLE && extensible_float);
        let pcm = tag == WAVE_FORMAT_PCM || (tag == WAVE_FORMAT_EXTENSIBLE && !extensible_float);
        match (float, pcm, bits) {
            (true, _, 32) => Some(Self::F32),
            (_, true, 16) => Some(Self::I16),
            (_, true, 24) => Some(Self::I24),
            (_, true, 32) => Some(Self::I32),
            _ => None,
        }
    }

    pub fn bytes(self) -> usize {
        match self {
            Self::I16 => 2,
            Self::I24 => 3,
            Self::F32 | Self::I32 => 4,
        }
    }

    /// One sample as -1.0 … 1.0 (`b` holds exactly `self.bytes()` bytes, little-endian).
    fn read(self, b: &[u8]) -> f32 {
        match self {
            Self::F32 => f32::from_le_bytes([b[0], b[1], b[2], b[3]]),
            Self::I16 => i16::from_le_bytes([b[0], b[1]]) as f32 / 32768.0,
            Self::I24 => (i32::from_le_bytes([0, b[0], b[1], b[2]]) >> 8) as f32 / 8_388_608.0,
            Self::I32 => i32::from_le_bytes([b[0], b[1], b[2], b[3]]) as f32 / 2_147_483_648.0,
        }
    }
}

pub fn to_i16(x: f32) -> i16 {
    (x * 32768.0).round().clamp(-32768.0, 32767.0) as i16
}

/// The device's frames in, Stenny's samples out.
pub struct Converter {
    channels: usize,
    format: SampleFormat,
    resampler: Option<Resampler>, // None: the device runs at 16 kHz already
    mono: Vec<f32>,
}

impl Converter {
    pub fn new(rate: u32, channels: u16, format: SampleFormat) -> Self {
        Converter {
            channels: channels.max(1) as usize,
            format,
            resampler: (rate != OUT_RATE).then(|| Resampler::new(rate, OUT_RATE)),
            mono: Vec::new(),
        }
    }

    /// Converts whole frames (`data.len()` a multiple of channels × sample size) and appends to `out`.
    pub fn process(&mut self, data: &[u8], out: &mut Vec<i16>) {
        let size = self.format.bytes();
        self.mono.clear();
        for frame in data.chunks_exact(size * self.channels) {
            let sum: f32 = frame.chunks_exact(size).map(|s| self.format.read(s)).sum();
            self.mono.push(sum / self.channels as f32);
        }
        match &mut self.resampler {
            Some(r) => r.process(&self.mono, out),
            None => out.extend(self.mono.iter().map(|&x| to_i16(x))),
        }
    }

    /// The samples still held back by the filter (a few milliseconds), at the end of a recording.
    pub fn flush(&mut self, out: &mut Vec<i16>) {
        if let Some(r) = &mut self.resampler {
            r.flush(out);
        }
    }
}

/// A streaming resampler: each output sample is the input around its position, weighted with a Blackman-windowed
/// sinc whose cutoff sits just under the lower of the two Nyquist frequencies (no aliasing when going down).
pub struct Resampler {
    step: f64,         // input samples per output sample
    fc: f64,           // cutoff, in cycles per input sample
    half: usize,       // the filter reaches this many input samples to each side
    history: Vec<f32>, // input not yet behind the filter's reach; starts with `half` zeros
    pos: f64,          // where the next output sample sits, as an index into `history`
    fed: u64,          // input samples so far
    made: u64,         // output samples so far
}

impl Resampler {
    pub fn new(from: u32, to: u32) -> Self {
        let fc = 0.5 * 0.92 * (to.min(from) as f64 / from as f64);
        let half = (10.0 / (2.0 * fc)).ceil() as usize; // ten zero crossings to each side
        Resampler { step: from as f64 / to as f64, fc, half, history: vec![0.0; half], pos: half as f64, fed: 0, made: 0 }
    }

    fn kernel(&self, d: f64) -> f64 {
        let x = 2.0 * self.fc * d;
        let sinc = if x.abs() < 1e-9 { 1.0 } else { (std::f64::consts::PI * x).sin() / (std::f64::consts::PI * x) };
        let w = std::f64::consts::PI * d / self.half as f64;
        let window = 0.42 + 0.5 * w.cos() + 0.08 * (2.0 * w).cos();
        2.0 * self.fc * sinc * window
    }

    pub fn process(&mut self, input: &[f32], out: &mut Vec<i16>) {
        self.fed += input.len() as u64;
        self.history.extend_from_slice(input);
        while (self.pos.floor() as usize) + self.half < self.history.len() {
            let center = self.pos.floor() as usize;
            let mut acc = 0.0;
            for k in center + 1 - self.half..=center + self.half {
                let d = self.pos - k as f64;
                if d.abs() < self.half as f64 {
                    acc += self.history[k] as f64 * self.kernel(d);
                }
            }
            out.push(to_i16(acc as f32));
            self.made += 1;
            self.pos += self.step;
        }
        // keep only what the next output samples still reach
        let keep_from = (self.pos.floor() as usize + 1).saturating_sub(self.half).min(self.history.len());
        if keep_from > 0 {
            self.history.drain(..keep_from);
            self.pos -= keep_from as f64;
        }
    }

    /// Pads with silence to get the last output samples out: as many in all as the input's length calls for.
    pub fn flush(&mut self, out: &mut Vec<i16>) {
        let expected = (self.fed as f64 / self.step).ceil() as u64;
        let pending = expected.saturating_sub(self.made) as usize;
        let (fed, start) = (self.fed, out.len());
        self.process(&vec![0.0; self.half + self.step.ceil() as usize + 1], out);
        out.truncate(start + pending);
        self.fed = fed;
        self.made = expected.max(self.made);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sine(rate: u32, freq: f64, amp: f64, seconds: f64) -> Vec<f32> {
        (0..(rate as f64 * seconds) as usize)
            .map(|i| (amp * (2.0 * std::f64::consts::PI * freq * i as f64 / rate as f64).sin()) as f32)
            .collect()
    }

    fn f32_bytes(samples: &[f32], channels: usize) -> Vec<u8> {
        samples.iter().flat_map(|s| std::iter::repeat(s.to_le_bytes()).take(channels).flatten()).collect()
    }

    fn rms(x: &[i16]) -> f64 {
        (x.iter().map(|&s| (s as f64 / 32767.0).powi(2)).sum::<f64>() / x.len() as f64).sqrt()
    }

    #[test]
    fn formats_from_the_wave_header() {
        assert_eq!(SampleFormat::from_wave(WAVE_FORMAT_EXTENSIBLE, 32, true), Some(SampleFormat::F32));
        assert_eq!(SampleFormat::from_wave(WAVE_FORMAT_EXTENSIBLE, 32, false), Some(SampleFormat::I32));
        assert_eq!(SampleFormat::from_wave(WAVE_FORMAT_EXTENSIBLE, 24, false), Some(SampleFormat::I24));
        assert_eq!(SampleFormat::from_wave(WAVE_FORMAT_PCM, 16, false), Some(SampleFormat::I16));
        assert_eq!(SampleFormat::from_wave(WAVE_FORMAT_IEEE_FLOAT, 32, false), Some(SampleFormat::F32));
        assert_eq!(SampleFormat::from_wave(WAVE_FORMAT_IEEE_FLOAT, 64, false), None);
        assert_eq!(SampleFormat::from_wave(0x0055, 16, false), None); // MP3: not something a microphone delivers
    }

    #[test]
    fn samples_are_read_with_their_sign() {
        assert_eq!(SampleFormat::I24.read(&[0x00, 0x00, 0x80]), -1.0);
        assert!((SampleFormat::I24.read(&[0xff, 0xff, 0x7f]) - 1.0).abs() < 1e-6);
        assert_eq!(SampleFormat::I16.read(&(-16384i16).to_le_bytes()), -0.5);
        assert_eq!(SampleFormat::I32.read(&i32::MIN.to_le_bytes()), -1.0);
        assert_eq!(to_i16(2.0), 32767);
        assert_eq!(to_i16(-2.0), -32768);
    }

    #[test]
    fn sixteen_khz_mono_passes_through() {
        let input: Vec<i16> = vec![0, 1000, -1000, 32767, -32768];
        let bytes: Vec<u8> = input.iter().flat_map(|s| s.to_le_bytes()).collect();
        let mut out = Vec::new();
        Converter::new(16_000, 1, SampleFormat::I16).process(&bytes, &mut out);
        assert_eq!(out, input); // bit for bit
    }

    #[test]
    fn stereo_48k_float_becomes_16k_mono_of_the_same_length_and_loudness() {
        let tone = sine(48_000, 440.0, 0.5, 2.0);
        let mut c = Converter::new(48_000, 2, SampleFormat::F32);
        let mut out = Vec::new();
        c.process(&f32_bytes(&tone, 2), &mut out);
        c.flush(&mut out);
        assert_eq!(out.len(), 32_000); // two seconds, nothing lost or added
        let middle = &out[1000..31_000];
        assert!((rms(middle) - 0.5 / 2f64.sqrt()).abs() < 0.01, "rms {}", rms(middle));
        // still 440 Hz: about 2 × 440 sign changes a second
        let crossings = middle.windows(2).filter(|w| (w[0] < 0) != (w[1] < 0)).count() as f64;
        assert!((crossings / (30_000.0 / 16_000.0) - 880.0).abs() < 5.0, "{crossings}");
    }

    #[test]
    fn what_16k_cannot_hold_is_filtered_out_not_folded_back() {
        let tone = sine(48_000, 12_000.0, 0.5, 1.0); // above 8 kHz, would alias to 4 kHz
        let mut out = Vec::new();
        Converter::new(48_000, 1, SampleFormat::F32).process(&f32_bytes(&tone, 1), &mut out);
        assert!(rms(&out[500..15_000]) < 0.005, "rms {}", rms(&out[500..15_000]));
    }

    #[test]
    fn common_rates_keep_the_length() {
        for rate in [8_000, 22_050, 44_100, 48_000, 96_000] {
            let input = sine(rate, 300.0, 0.3, 3.0);
            let mut c = Converter::new(rate, 1, SampleFormat::F32);
            let mut out = Vec::new();
            c.process(&f32_bytes(&input, 1), &mut out);
            c.flush(&mut out);
            assert!((out.len() as i64 - 48_000).abs() <= 1, "{rate}: {}", out.len());
        }
    }

    #[test]
    fn chunks_give_the_same_result_as_one_piece() {
        let input = f32_bytes(&sine(44_100, 1000.0, 0.4, 1.0), 2);
        let mut whole = Vec::new();
        Converter::new(44_100, 2, SampleFormat::F32).process(&input, &mut whole);
        let mut pieces = Vec::new();
        let mut c = Converter::new(44_100, 2, SampleFormat::F32);
        for chunk in input.chunks(8 * 441) {
            // 441 frames of 8 bytes, like WASAPI's 10 ms packets
            c.process(chunk, &mut pieces);
        }
        assert_eq!(whole, pieces);
    }
}
