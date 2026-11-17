static mut VALUE: i32 = 0;
fn bump() {
    for _ in 0..10000 {
        unsafe {
            let pointer = std::ptr::addr_of_mut!(VALUE);
            std::ptr::write_volatile(pointer, std::ptr::read_volatile(pointer) + 1);
        }
    }
}
fn main() {
    let one = std::thread::spawn(bump);
    let two = std::thread::spawn(bump);
    one.join().unwrap();
    two.join().unwrap();
}
