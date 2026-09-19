#!/usr/bin/env python3
"""Quantizacao de cores PPM (P6) paralela com multithreading.

A construcao e a reducao da octree permanecem seriais porque alteram o mesmo
estado. A substituicao final das cores e dividida em faixas independentes de
pixels e executada por ``threading.Thread``. Em CPython, o GIL impede que
bytecode CPU-bound execute simultaneamente em varios nucleos; esta versao serve
para estudar esse custo e comparar threads com a implementacao serial.

Uso:

    python3 color_quant_multithreading.py entrada.ppm 16 saida.ppm --workers 4
"""

from __future__ import annotations

import argparse
import sys
import threading
from collections import Counter
from math import isfinite
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
        read_ppm,
        write_ppm,
    )


SUPPORTED_WORKERS = (1, 2, 4, 8)
DEFAULT_WORKERS = 4
Color = tuple[int, int, int]


def _parse_positive_seconds(value: str) -> float:
    try:
        seconds = float(value.replace(",", "."))
    except ValueError as error:
        raise argparse.ArgumentTypeError("informe um numero de segundos valido") from error
    if not isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("o tempo deve ser positivo e finito")
    return seconds


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


def _histogram_range(
    pixels: bytearray,
    start_pixel: int,
    end_pixel: int,
) -> Counter[Color]:
    """Conta cores numa faixa usando somente estado local da thread."""
    histogram: Counter[Color] = Counter()
    for pixel_index in range(start_pixel, end_pixel):
        offset = pixel_index * 3
        histogram[(pixels[offset], pixels[offset + 1], pixels[offset + 2])] += 1
    return histogram


def _build_histogram_parallel(image: Image, workers: int) -> Counter[Color]:
    """Constroi histogramas locais em threads e os combina sem corridas."""
    ranges = _pixel_ranges(image.width * image.height, workers)
    if len(ranges) == 1:
        return _histogram_range(image.pixels, *ranges[0])

    partials: list[Counter[Color] | None] = [None] * len(ranges)
    errors: list[Exception] = []

    def worker(index: int, start: int, end: int) -> None:
        try:
            partials[index] = _histogram_range(image.pixels, start, end)
        except Exception as error:
            errors.append(error)

    threads = [
        threading.Thread(target=worker, args=(index, start, end))
        for index, (start, end) in enumerate(ranges)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]
    histogram: Counter[Color] = Counter()
    for partial in partials:
        if partial is not None:
            histogram.update(partial)
    return histogram


def _node_insert_count(
    root: OctreeNode,
    red: int,
    green: int,
    blue: int,
    count: int,
) -> OctreeNode:
    """Insere uma cor e sua frequencia acumulada na octree."""
    node = root
    for depth, bit in enumerate((128, 64, 32, 16, 8, 4, 2), start=1):
        index = (
            (1 if green & bit else 0) * 4
            + (1 if red & bit else 0) * 2
            + (1 if blue & bit else 0)
        )
        child = node.children[index]
        if child is None:
            child = OctreeNode(
                child_index=index,
                depth=depth,
                parent=node,
            )
            node.children[index] = child
            node.child_count += 1
        node = child

    node.red += red * count
    node.green += green * count
    node.blue += blue * count
    node.count += count
    return node


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

    errors: list[Exception] = []

    def worker(start: int, end: int) -> None:
        try:
            _replace_range(image.pixels, root, start, end)
        except Exception as error:
            errors.append(error)

    threads = [
        threading.Thread(target=worker, args=(start, end))
        for start, end in ranges
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


def color_quant(
    image: Image,
    n_colors: int,
    dither: bool = False,
    workers: int = DEFAULT_WORKERS,
) -> None:
    """Quantiza ``image`` usando threads na etapa independente por pixel."""
    if n_colors < 1:
        raise ValueError("A quantidade de cores deve ser maior que zero")
    if workers not in SUPPORTED_WORKERS:
        raise ValueError("A quantidade de threads deve ser 1, 2, 4 ou 8")

    root = OctreeNode()
    heap = NodeHeap()
    histogram = _build_histogram_parallel(image, workers)

    # A combinacao e serial: a octree e o heap sao estados mutaveis globais.
    for (red, green, blue), count in histogram.items():
        leaf = _node_insert_count(root, red, green, blue, count)
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
        description="Quantiza PPM P6 em paralelo usando multithreading."
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
        choices=SUPPORTED_WORKERS,
        default=DEFAULT_WORKERS,
        help=f"numero de threads: 1, 2, 4 ou 8 (padrao: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--baseline-seconds",
        type=_parse_positive_seconds,
        help="tempo da quantizacao Python serial, para speedup e eficiencia",
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

    print(f"Threads: {args.workers}")
    if args.dither:
        print("Aviso: a difusao de erro foi executada serialmente.")
    print(f"Tempo de execucao da quantizacao: {elapsed:.9f} segundos")
    if args.baseline_seconds is not None and elapsed > 0:
        speedup = args.baseline_seconds / elapsed
        print(f"Speedup versus Python serial: {speedup:.4f}")
        print(f"Eficiencia: {speedup / args.workers:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
