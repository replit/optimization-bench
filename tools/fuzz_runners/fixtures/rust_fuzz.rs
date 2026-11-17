#![no_main]
use libfuzzer_sys::fuzz_target;
fuzz_target!(|data: &[u8]| {
    if let Some(n) = data.first() {
        let result = if *n == 0 { 0 } else if *n < 128 { 1 } else { 2 };
        assert!(result <= 2);
    }
});
