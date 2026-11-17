# Case inventory

Each row specifies one call to `np.quantile` with `method="linear"`. Exact preparation, strides and provenance are in `manifest.json`.

v1.1: every taxi and electricity row except `small-table` and `strided-subset` is a call inside the single performance case `natural-workload-warm-batch` (one resident process; inputs prepared before timing, results serialized after). The remaining rows are separate correctness cases.

| Case | Purpose | Input shape / dtype | q count | axis | keepdims |
|---|---|---|---:|---|---|
| `taxi-2023-01--distance-median` | Median trip distance across selected rides. | `(1000000,)` / float64 | 1 | `None` | False |
| `taxi-2023-01--distance-bins` | Seventeen boundaries for distance bins, retaining ties. | `(1000000,)` / float32 | 17 | `None` | False |
| `taxi-2023-01--column-quartiles` | Quartiles for each distance or monetary column. | `(1000000, 4)` / float64 | 3 | `0` | False |
| `taxi-2023-01--column-tails-fortran` | Per-column lower, median and upper tail summaries. | `(1000000, 4)` / float64 | 3 | `0` | True |
| `taxi-2023-01--strided-fares` | Fare quantiles for every third selected ride; repeated unsorted q. | `(333334,)` / float64 | 4 | `-1` | False |
| `taxi-2023-01--small-table` | A smaller table summary using single precision inputs. | `(25000, 4)` / float32 | 3 | `0` | False |
| `taxi-2023-07--distance-median` | Median trip distance across selected rides. | `(1000000,)` / float64 | 1 | `None` | False |
| `taxi-2023-07--distance-bins` | Seventeen boundaries for distance bins, retaining ties. | `(1000000,)` / float32 | 17 | `None` | False |
| `taxi-2023-07--column-quartiles` | Quartiles for each distance or monetary column. | `(1000000, 4)` / float64 | 3 | `0` | False |
| `taxi-2023-07--column-tails-fortran` | Per-column lower, median and upper tail summaries. | `(1000000, 4)` / float64 | 3 | `0` | True |
| `taxi-2023-07--strided-fares` | Fare quantiles for every third selected ride; repeated unsorted q. | `(333334,)` / float64 | 4 | `-1` | False |
| `taxi-2023-07--small-table` | A smaller table summary using single precision inputs. | `(25000, 4)` / float32 | 3 | `0` | False |
| `taxi-2023-03--distance-median` | Median trip distance across selected rides. | `(1000000,)` / float64 | 1 | `None` | False |
| `taxi-2023-03--distance-bins` | Seventeen boundaries for distance bins, retaining ties. | `(1000000,)` / float32 | 17 | `None` | False |
| `taxi-2023-03--column-quartiles` | Quartiles for each distance or monetary column. | `(1000000, 4)` / float64 | 3 | `0` | False |
| `taxi-2023-03--column-tails-fortran` | Per-column lower, median and upper tail summaries. | `(1000000, 4)` / float64 | 3 | `0` | True |
| `taxi-2023-03--strided-fares` | Fare quantiles for every third selected ride; repeated unsorted q. | `(333334,)` / float64 | 4 | `-1` | False |
| `taxi-2023-03--small-table` | A smaller table summary using single precision inputs. | `(25000, 4)` / float32 | 3 | `0` | False |
| `taxi-2023-04--distance-median` | Median trip distance across selected rides. | `(1000000,)` / float64 | 1 | `None` | False |
| `taxi-2023-04--distance-bins` | Seventeen boundaries for distance bins, retaining ties. | `(1000000,)` / float32 | 17 | `None` | False |
| `taxi-2023-04--column-quartiles` | Quartiles for each distance or monetary column. | `(1000000, 4)` / float64 | 3 | `0` | False |
| `taxi-2023-04--column-tails-fortran` | Per-column lower, median and upper tail summaries. | `(1000000, 4)` / float64 | 3 | `0` | True |
| `taxi-2023-04--strided-fares` | Fare quantiles for every third selected ride; repeated unsorted q. | `(333334,)` / float64 | 4 | `-1` | False |
| `taxi-2023-04--small-table` | A smaller table summary using single precision inputs. | `(25000, 4)` / float32 | 3 | `0` | False |
| `taxi-2023-05--distance-median` | Median trip distance across selected rides. | `(1000000,)` / float64 | 1 | `None` | False |
| `taxi-2023-05--distance-bins` | Seventeen boundaries for distance bins, retaining ties. | `(1000000,)` / float32 | 17 | `None` | False |
| `taxi-2023-05--column-quartiles` | Quartiles for each distance or monetary column. | `(1000000, 4)` / float64 | 3 | `0` | False |
| `taxi-2023-05--column-tails-fortran` | Per-column lower, median and upper tail summaries. | `(1000000, 4)` / float64 | 3 | `0` | True |
| `taxi-2023-05--strided-fares` | Fare quantiles for every third selected ride; repeated unsorted q. | `(333334,)` / float64 | 4 | `-1` | False |
| `taxi-2023-05--small-table` | A smaller table summary using single precision inputs. | `(25000, 4)` / float32 | 3 | `0` | False |
| `taxi-2023-09--distance-median` | Median trip distance across selected rides. | `(1000000,)` / float64 | 1 | `None` | False |
| `taxi-2023-09--distance-bins` | Seventeen boundaries for distance bins, retaining ties. | `(1000000,)` / float32 | 17 | `None` | False |
| `taxi-2023-09--column-quartiles` | Quartiles for each distance or monetary column. | `(1000000, 4)` / float64 | 3 | `0` | False |
| `taxi-2023-09--column-tails-fortran` | Per-column lower, median and upper tail summaries. | `(1000000, 4)` / float64 | 3 | `0` | True |
| `taxi-2023-09--strided-fares` | Fare quantiles for every third selected ride; repeated unsorted q. | `(333334,)` / float64 | 4 | `-1` | False |
| `taxi-2023-09--small-table` | A smaller table summary using single precision inputs. | `(25000, 4)` / float32 | 3 | `0` | False |
| `taxi-2023-10--distance-median` | Median trip distance across selected rides. | `(1000000,)` / float64 | 1 | `None` | False |
| `taxi-2023-10--distance-bins` | Seventeen boundaries for distance bins, retaining ties. | `(1000000,)` / float32 | 17 | `None` | False |
| `taxi-2023-10--column-quartiles` | Quartiles for each distance or monetary column. | `(1000000, 4)` / float64 | 3 | `0` | False |
| `taxi-2023-10--column-tails-fortran` | Per-column lower, median and upper tail summaries. | `(1000000, 4)` / float64 | 3 | `0` | True |
| `taxi-2023-10--strided-fares` | Fare quantiles for every third selected ride; repeated unsorted q. | `(333334,)` / float64 | 4 | `-1` | False |
| `taxi-2023-10--small-table` | A smaller table summary using single precision inputs. | `(25000, 4)` / float32 | 3 | `0` | False |
| `taxi-2023-11--distance-median` | Median trip distance across selected rides. | `(1000000,)` / float64 | 1 | `None` | False |
| `taxi-2023-11--distance-bins` | Seventeen boundaries for distance bins, retaining ties. | `(1000000,)` / float32 | 17 | `None` | False |
| `taxi-2023-11--column-quartiles` | Quartiles for each distance or monetary column. | `(1000000, 4)` / float64 | 3 | `0` | False |
| `taxi-2023-11--column-tails-fortran` | Per-column lower, median and upper tail summaries. | `(1000000, 4)` / float64 | 3 | `0` | True |
| `taxi-2023-11--strided-fares` | Fare quantiles for every third selected ride; repeated unsorted q. | `(333334,)` / float64 | 4 | `-1` | False |
| `taxi-2023-11--small-table` | A smaller table summary using single precision inputs. | `(25000, 4)` / float32 | 3 | `0` | False |
| `electricity-2012-block1--customer-quartiles` | Demand quartiles over time for each customer. | `(17568, 148)` / float64 | 3 | `0` | False |
| `electricity-2012-block1--cross-customer-tail` | 90th percentile of customer demand at each timestamp. | `(17568, 148)` / float32 | 1 | `1` | False |
| `electricity-2012-block1--transposed-time` | Per-customer demand tails from a transposed view. | `(148, 17568)` / float32 | 3 | `-1` | False |
| `electricity-2012-block1--strided-subset` | Time quantiles for alternate customers and every fourth timestamp. | `(4392, 74)` / float64 | 3 | `0` | False |
| `electricity-2012-block1--pooled-bins` | Seventeen demand boundaries pooled across customers and time. | `(17568, 148)` / float32 | 17 | `[0, 1]` | False |
| `electricity-2012-block2--customer-quartiles` | Demand quartiles over time for each customer. | `(17568, 148)` / float64 | 3 | `0` | False |
| `electricity-2012-block2--cross-customer-tail` | 90th percentile of customer demand at each timestamp. | `(17568, 148)` / float32 | 1 | `1` | False |
| `electricity-2012-block2--transposed-time` | Per-customer demand tails from a transposed view. | `(148, 17568)` / float32 | 3 | `-1` | False |
| `electricity-2012-block2--strided-subset` | Time quantiles for alternate customers and every fourth timestamp. | `(4392, 74)` / float64 | 3 | `0` | False |
| `electricity-2012-block2--pooled-bins` | Seventeen demand boundaries pooled across customers and time. | `(17568, 148)` / float32 | 17 | `[0, 1]` | False |
| `generic-cube--time-quantile-maps` | Low, median and high generic scalar field over time at each grid cell. | `(180, 32, 64)` / float32 | 3 | `0` | False |
| `generic-cube--transposed-time-view` | Time tails of a transposed generic scalar field array. | `(32, 64, 180)` / float32 | 2 | `-1` | False |
| `generic-cube--spatial-median` | Unweighted grid-cell median of generic scalar field at each timestamp. | `(180, 32, 64)` / float32 | 1 | `[1, 2]` | True |
| `generic-cube--strided-grid` | Time quantile boundaries of a sliced generic scalar field grid. | `(90, 16, 32)` / float64 | 17 | `0` | False |
| `generic-cube--global-tails` | Unweighted pooled tails across the entire generic scalar field array. | `(180, 32, 64)` / float32 | 3 | `None` | False |
| `upstream-style-vector--quartiles` | Basic ordered-vector quartiles. | `(10000,)` / float32 | 2 | `None` | False |
| `upstream-style-table--columns` | Small dense-table quantile example. | `(1000, 3)` / float64 | 2 | `0` | False |
| `correctness-linear-not-nearest--check` | linear not nearest | `(4,)` / float64 | 2 | `None` | False |
| `correctness-repeated-unsorted-q--check` | repeated unsorted q | `(5,)` / float64 | 5 | `None` | False |
| `correctness-nan-propagation--check` | nan propagation | `(2, 3)` / float64 | 3 | `1` | False |
| `correctness-singletons--check` | singletons | `(2, 1)` / float32 | 3 | `-1` | True |
| `correctness-signed-zeros--check` | signed zeros | `(4,)` / float64 | 3 | `None` | False |
| `correctness-tuple-keepdims--check` | tuple keepdims | `(2, 3, 4)` / float32 | 2 | `[0, 2]` | True |
| `correctness-scalar-q--check` | scalar q | `(4,)` / float32 | 1 | `None` | False |
| `correctness-tuple-keepdims--negative-stride` | Reverse a dimension before a tuple-axis reduction. | `(2, 3, 4)` / float32 | 2 | `[0, 2]` | False |
