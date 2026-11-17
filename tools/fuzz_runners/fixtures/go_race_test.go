package fixture

import (
	"sync"
	"testing"
)

func TestRace(t *testing.T) {
	value := 0
	var wg sync.WaitGroup
	wg.Add(2)
	for i := 0; i < 2; i++ {
		go func() {
			defer wg.Done()
			for j := 0; j < 1000; j++ { value++ }
		}()
	}
	wg.Wait()
	_ = value
}
