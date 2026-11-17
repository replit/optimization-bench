use std::sync::atomic::{AtomicI32, Ordering};
static VALUE: AtomicI32 = AtomicI32::new(0);
fn main() {
    let first = std::thread::spawn(|| { VALUE.fetch_add(1, Ordering::SeqCst); });
    let second = std::thread::spawn(|| { VALUE.fetch_add(1, Ordering::SeqCst); });
    first.join().unwrap();
    second.join().unwrap();
}
