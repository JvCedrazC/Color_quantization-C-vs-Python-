#!/usr/bin/env python3
"""Quantizacao de cores PPM (P6) com threads limitadas pelo GIL.

A construcao e a reducao da octree permanecem seriais porque alteram o mesmo
estado. A substituicao final das cores e dividida em faixas independentes de
pixels e executada por ``ThreadPoolExecutor``. Em CPython, o GIL impede que
bytecode CPU-bound execute simultaneamente em varios nucleos; esta versao serve
para estudar esse custo e comparar threads com a implementacao serial.

Uso:

    python3 color_quant_GIL.py entrada.ppm 16 saida.ppm --workers 4
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from typing import Sequence

if __package__:
    from .color_quant import (
        Image,
        NodeHeap,
        OctreeNode,
        _error_diffuse,
        _find_palette_node,
        node_fold,
        node_insert,
        read_ppm,
        write_ppm,
    )
else:
    from color_quant import (
        Image,
        NodeHeap,
        OctreeNode,
        _error_diffuse,
        _find_palette_node,
        node_fold,
        node_insert,
        read_ppm,
        write_ppm,
    )


DEFAULT_WORKERS = min(32, os.cpu_count() or 1)


def _replace_range(
    pixels: bytearray,
    root: OctreeNode,
    start_pixel: int,
    end_pixel: int,
) -> None:
    """Substitui uma faixa exclusiva de pixels; nao compartilha escritas."""
    for pixel_index in range(start_pixel, end_pixel):
        offset = pixel_index * 3
        node = _find_palette_node(
            root,
            pixels[offset],
            pixels[offset + 1],
            pixels[offset + 2],
        )
        pixels[offset] = node.red
        pixels[offset + 1] = node.green
        pixels[offset + 2] = node.blue


def _pixel_ranges(pixel_count: int, workers: int) -> list[tuple[int, int]]:
    """Divide os pixels em faixas contiguas e balanceadas."""
    active_workers = min(workers, pixel_count)
    base, remainder = divmod(pixel_count, active_workers)
    ranges: list[tuple[int, int]] = []
    start = 0

    for worker_index in range(active_workers):
        size = base + (1 if worker_index < remainder else 0)
        end = start + size
        ranges.append((start, end))
        start = end

    return ranges


def _replace_colors_parallel(
    image: Image,
    root: OctreeNode,
    workers: int,
) -> None:
    """Executa a substituicao independente de cores usando threads Python."""
    ranges = _pixel_ranges(image.width * image.height, workers)
    if len(ranges) == 1:
        _replace_range(image.pixels, root, *ranges[0])
        return

    with ThreadPoolExecutor(
        max_workers=len(ranges),
        thread_name_prefix="color-quant-gil",
    ) as executor:
        futures = [
            executor.submit(_replace_range, image.pixels, root, start, end)
            for start, end in ranges
        ]
        for future in futures:
            future.result()


def color_quant(
    image: Image,
    n_colors: int,
    dither: bool = False,
    workers: int = DEFAULT_WORKERS,
) -> None:
    """Quantiza ``image`` usando threads na etapa independente por pixel."""
    if n_colors < 1:
        raise ValueError("A quantidade de cores deve ser maior que zero")
    if workers < 1:
        raise ValueError("A quantidade de workers deve ser maior que zero")

    root = OctreeNode()
    heap = NodeHeap()
    pixels = image.pixels

    # A octree e o heap sao mutaveis e compartilhados; esta etapa e serial.
    for offset in range(0, len(pixels), 3):
        leaf = node_insert(
            root,
            pixels[offset],
            pixels[offset + 1],
            pixels[offset + 2],
        )
        heap.add(leaf)

    while len(heap) > n_colors:
        heap.add(node_fold(heap.pop()))

    palette = heap.palette()
    for node in palette:
        node.red = int(node.red / node.count + 0.5)
        node.green = int(node.green / node.count + 0.5)
        node.blue = int(node.blue / node.count + 0.5)

    if dither:
        # A difusao propaga erro para pixels vizinhos e nao pode ser dividida
        # desta forma sem alterar o resultado do algoritmo.
        _error_diffuse(image, palette)
    else:
        _replace_colors_parallel(image, root, workers)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quantiza PPM P6 com threads Python limitadas pelo GIL."
    )
    parser.add_argument("input", type=Path, help="imagem PPM P6 de entrada")
    parser.add_argument("colors", type=int, help="quantidade maxima de cores")
    parser.add_argument("output", type=Path, help="imagem PPM P6 de saida")
    parser.add_argument(
        "--dither",
        action="store_true",
        help="aplica difusao de erro serial na imagem resultante",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"numero de threads (padrao: {DEFAULT_WORKERS})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        image = read_ppm(args.input)
        start = perf_counter()
        color_quant(image, args.colors, args.dither, args.workers)
        elapsed = perf_counter() - start
        write_ppm(image, args.output)
    except (OSError, ValueError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1

    print(f"Threads sob o GIL: {args.workers}")
    if args.dither:
        print("Aviso: a difusao de erro foi executada serialmente.")
    print(f"Tempo de execucao da quantizacao: {elapsed:.9f} segundos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
