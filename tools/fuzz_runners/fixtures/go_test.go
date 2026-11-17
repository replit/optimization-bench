package fixture

import (
	"testing"
	"pgregory.net/rapid"
)

func TestProperty(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		n := rapid.Int().Draw(t, "n")
		c := classify(n)
		if (n < 0 && c != -1) || (n == 0 && c != 0) || (n > 0 && c != 1) { t.Fatal("sign") }
	})
}

func FuzzClassify(f *testing.F) {
	f.Add(-1); f.Add(0); f.Add(1)
	f.Fuzz(func(t *testing.T, n int) {
		if c := classify(n); c < -1 || c > 1 { t.Fatal("range") }
	})
}
