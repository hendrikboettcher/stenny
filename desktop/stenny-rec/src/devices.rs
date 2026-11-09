//! What the engine needs to know about a microphone, from what Windows says about its endpoint.

/// One active capture endpoint, as `--list-json` reports it.
#[derive(Debug, PartialEq)]
pub struct Device {
    pub name: String,
    /// the system's default input (Settings ▸ System ▸ Sound ▸ Input)
    pub default: bool,
    /// the computer's own microphone: the one the backup microphone "auto" takes
    pub builtin: bool,
    /// a device an app installs for itself (Microsoft Teams Audio, virtual cables): it hears no room
    pub r#virtual: bool,
}

/// How the endpoint's audio hardware is attached, from the id of the device its topology connects to
/// (`\\?\HDAUDIO#FUNC_01&VEN_10EC…`, `{2}.\\?\usb#vid_046d…`, `\\?\ROOT#MEDIA#0000…`).
#[derive(Debug, PartialEq)]
pub enum Bus {
    Usb,
    Bluetooth,
    /// a software device (ROOT\…): what virtual cables and meeting apps install
    Virtual,
    /// on the computer's own bus (HD Audio, Intel SST, ACPI, PCI …)
    Internal,
    Unknown,
}

pub fn bus(topology_device_id: &str) -> Bus {
    let id = topology_device_id.to_uppercase();
    let path = id.rsplit_once(r"\\?\").map_or(id.as_str(), |(_, p)| p);
    let enumerator = path.split(['#', '\\']).next().unwrap_or("");
    match enumerator {
        "" => Bus::Unknown,
        "USB" => Bus::Usb,
        e if e.starts_with("BTH") => Bus::Bluetooth,
        "ROOT" => Bus::Virtual,
        "SWD" => Bus::Unknown, // software devices of all kinds, also some Bluetooth LE Audio
        _ => Bus::Internal,
    }
}

/// EndpointFormFactor (PKEY_AudioEndpoint_FormFactor): Microphone
pub const FORM_FACTOR_MICROPHONE: u32 = 4;

/// The computer's own microphone: on its own bus and a microphone (not a line input or a headset). A microphone
/// plugged into the computer's jack looks the same to Windows; Stenny can't tell those apart.
pub fn is_builtin(bus: &Bus, form_factor: Option<u32>) -> bool {
    *bus == Bus::Internal && form_factor == Some(FORM_FACTOR_MICROPHONE)
}

/// The message when `--device` names a microphone that isn't there, as the Swift recorder says it.
pub fn not_found(name: &str, available: &[String]) -> String {
    format!("no input device named \"{name}\" (available: {})", available.join(", "))
}

fn json_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// `--list-json`: one JSON array, UTF-8.
pub fn to_json(devices: &[Device]) -> String {
    let items: Vec<String> = devices
        .iter()
        .map(|d| {
            format!(
                "{{\"name\":{},\"default\":{},\"builtin\":{},\"virtual\":{}}}",
                json_string(&d.name),
                d.default,
                d.builtin,
                d.r#virtual
            )
        })
        .collect();
    format!("[{}]", items.join(","))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn buses_from_topology_ids() {
        assert_eq!(bus(r"\\?\HDAUDIO#FUNC_01&VEN_10EC&DEV_0257&SUBSYS_17AA225D&REV_1000#4&2b4b2b3c&0&0001#{6994ad04-93ef-11d0-a3cc-00a0c9223196}\emic"), Bus::Internal);
        assert_eq!(bus(r"\\?\INTELAUDIO#DSP_INTC&DEV_0123#5&1a2b3c&0&0000#{6994ad04-93ef-11d0-a3cc-00a0c9223196}\micarray"), Bus::Internal);
        assert_eq!(bus(r"{2}.\\?\usb#vid_046d&pid_0825&mi_02#7&1f3e1e7&0&0002#{6994ad04-93ef-11d0-a3cc-00a0c9223196}\global"), Bus::Usb);
        assert_eq!(bus(r"{2}.\\?\bthhfenum#{0000111e-0000-1000-8000-00805f9b34fb}_vid&0002004c_pid&200e#8&1b2c3d4e&0&ac1f74000000_c00000000#{6994ad04-93ef-11d0-a3cc-00a0c9223196}\bthhfaudio"), Bus::Bluetooth);
        assert_eq!(bus(r"\\?\ROOT#MEDIA#0000#{6994ad04-93ef-11d0-a3cc-00a0c9223196}\wave"), Bus::Virtual);
        assert_eq!(bus(r"\\?\SWD#DRIVERENUM#something#{6994ad04-93ef-11d0-a3cc-00a0c9223196}"), Bus::Unknown);
        assert_eq!(bus(""), Bus::Unknown);
    }

    #[test]
    fn builtin_means_an_internal_microphone() {
        assert!(is_builtin(&Bus::Internal, Some(FORM_FACTOR_MICROPHONE)));
        assert!(!is_builtin(&Bus::Internal, Some(2))); // line in
        assert!(!is_builtin(&Bus::Usb, Some(FORM_FACTOR_MICROPHONE)));
        assert!(!is_builtin(&Bus::Virtual, Some(FORM_FACTOR_MICROPHONE)));
        assert!(!is_builtin(&Bus::Internal, None));
    }

    #[test]
    fn json_is_valid_for_any_name() {
        let devices = vec![
            Device { name: "Microphone Array (Intel® Smart Sound)".into(), default: true, builtin: true, r#virtual: false },
            Device { name: "Mic \"A\" \\ B\u{1}".into(), default: false, builtin: false, r#virtual: true },
        ];
        assert_eq!(
            to_json(&devices),
            "[{\"name\":\"Microphone Array (Intel® Smart Sound)\",\"default\":true,\"builtin\":true,\"virtual\":false},\
             {\"name\":\"Mic \\\"A\\\" \\\\ B\\u0001\",\"default\":false,\"builtin\":false,\"virtual\":true}]"
        );
        assert_eq!(to_json(&[]), "[]");
    }

    #[test]
    fn a_missing_device_names_the_ones_there_are() {
        assert_eq!(
            not_found("USB Mic", &["Microphone (Realtek(R) Audio)".into(), "Headset (AirPods)".into()]),
            "no input device named \"USB Mic\" (available: Microphone (Realtek(R) Audio), Headset (AirPods))"
        );
    }
}
