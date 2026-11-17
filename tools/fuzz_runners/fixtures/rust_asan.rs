fn main() {
    let data = Box::new([0u8; 1]);
    let n = std::env::args().count();
    unsafe { std::ptr::write_volatile(data.as_ptr().add(n) as *mut u8, 42); }
}
