fn main() {
    let data = [0u8; 1];
    unsafe { let _ = std::ptr::read_volatile(data.as_ptr().add(1)); }
}
