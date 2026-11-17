fn classify(n: i32) -> i32 {
    if n < 0 { -1 } else if n == 0 { 0 } else { 1 }
}
#[cfg(test)]
mod tests {
    use proptest::prelude::*;
    proptest! {
        #[test]
        fn sign(n in any::<i32>()) {
            prop_assert_eq!(super::classify(n), n.signum());
        }
    }
}
