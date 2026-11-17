use std::sync::atomic::{AtomicI32, Ordering};
static VALUE: AtomicI32 = AtomicI32::new(0);
fn main() {
    let one = std::thread::spawn(|| { VALUE.fetch_add(1, Ordering::SeqCst); });
    let two = std::thread::spawn(|| { VALUE.fetch_add(1, Ordering::SeqCst); });
    one.join().unwrap();
    two.join().unwrap();
}
