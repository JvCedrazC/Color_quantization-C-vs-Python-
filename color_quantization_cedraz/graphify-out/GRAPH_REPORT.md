# Graph Report - color_quantization_cedraz  (2026-08-20)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 22 nodes · 56 edges · 3 communities
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2

## God Nodes (most connected - your core abstractions)
1. `color_quant()` - 11 edges
2. `down_heap()` - 6 edges
3. `heap_add()` - 6 edges
4. `pop_heap()` - 5 edges
5. `up_heap()` - 5 edges
6. `read_ppm()` - 5 edges
7. `error_diffuse()` - 4 edges
8. `node_insert()` - 4 edges
9. `node_new()` - 4 edges
10. `cmp_node()` - 4 edges

## Surprising Connections (you probably didn't know these)
- `color_quant()` --calls--> `heap_add()`  [EXTRACTED]
  color_quant.c → color_quant.c  _Bridges community 0 → community 1_
- `color_quant()` --references--> `image`  [EXTRACTED]
  color_quant.c →   _Bridges community 0 → community 2_

## Import Cycles
- None detected.

## Communities (3 total, 0 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.50
Nodes (7): color_quant(), color_replace(), error_diffuse(), node_fold(), node_free(), node_insert(), node_new()

### Community 1 - "Community 1"
Cohesion: 0.67
Nodes (7): cmp_node(), down_heap(), heap_add(), pop_heap(), up_heap(), node_heap, oct_node

### Community 2 - "Community 2"
Cohesion: 0.38
Nodes (7): img_new(), main(), read_num(), read_ppm(), write_ppm(), FILE, image

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `color_quant()` connect `Community 0` to `Community 1`, `Community 2`?**
  _High betweenness centrality (0.128) - this node is a cross-community bridge._
- **Why does `read_num()` connect `Community 2` to `Community 0`?**
  _High betweenness centrality (0.095) - this node is a cross-community bridge._
- **Why does `heap_add()` connect `Community 1` to `Community 0`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._