fn classify(n: i32) -> i32 {
    if n < 0 { -1 } else if n == 0 { 0 } else { 1 }
}
fn main() {
    for n in [-1, 0, 1] { assert_eq!(classify(n), n.signum()); }
}
