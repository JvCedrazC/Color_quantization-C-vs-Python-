#!/usr/bin/env python3
"""Quantizacao PPM (P6) paralela com multiprocessing.

Esta variante usa ``ProcessPoolExecutor`` para executar em nucleos distintos,
sem a limitacao do GIL entre workers. Cada processo constroi um histograma local;
os histogramas sao combinados no processo principal antes da reducao da octree.
A substituicao final tambem ocorre em processos sobre blocos independentes.

Uso:

    python3 color_quant_multiprocessing.py entrada.ppm 16 saida.ppm --workers 4
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
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
_PROCESS_ROOT: OctreeNode | None = None


def _parse_positive_seconds(value: str) -> float:
    try:
        seconds = float(value.replace(",", "."))
    except ValueError as error:
        raise argparse.ArgumentTypeError("informe um numero de segundos valido") from error
    if not isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("o tempo deve ser positivo e finito")
    return seconds


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


def _pixel_chunks(image: Image, workers: int) -> list[bytes]:
    pixels = image.pixels
    return [
        bytes(pixels[start * 3 : end * 3])
        for start, end in _pixel_ranges(image.width * image.height, workers)
    ]


def _histogram_chunk(chunk: bytes) -> Counter[Color]:
    """Constroi um histograma local dentro de um processo worker."""
    histogram: Counter[Color] = Counter()
    for offset in range(0, len(chunk), 3):
        histogram[(chunk[offset], chunk[offset + 1], chunk[offset + 2])] += 1
    return histogram


def _build_histogram_multiprocessing(
    image: Image,
    workers: int,
) -> Counter[Color]:
    chunks = _pixel_chunks(image, workers)
    if len(chunks) == 1:
        return _histogram_chunk(chunks[0])

    histogram: Counter[Color] = Counter()
    with ProcessPoolExecutor(max_workers=len(chunks)) as executor:
        for local_histogram in executor.map(_histogram_chunk, chunks):
            histogram.update(local_histogram)
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


def _initialize_replacement_worker(root: OctreeNode) -> None:
    """Instala uma copia somente-leitura da octree em cada processo."""
    global _PROCESS_ROOT
    _PROCESS_ROOT = root


def _replace_chunk(chunk: bytes) -> bytes:
    """Substitui as cores de um bloco independente em outro processo."""
    if _PROCESS_ROOT is None:
        raise RuntimeError("Worker de substituicao nao foi inicializado")

    result = bytearray(chunk)
    for offset in range(0, len(result), 3):
        node = _find_palette_node(
            _PROCESS_ROOT,
            result[offset],
            result[offset + 1],
            result[offset + 2],
        )
        result[offset] = node.red
        result[offset + 1] = node.green
        result[offset + 2] = node.blue
    return bytes(result)


def _replace_colors_multiprocessing(
    image: Image,
    root: OctreeNode,
    workers: int,
) -> None:
    chunks = _pixel_chunks(image, workers)
    if len(chunks) == 1:
        _initialize_replacement_worker(root)
        image.pixels[:] = _replace_chunk(chunks[0])
        return

    with ProcessPoolExecutor(
        max_workers=len(chunks),
        initializer=_initialize_replacement_worker,
        initargs=(root,),
    ) as executor:
        converted_chunks = executor.map(_replace_chunk, chunks)
        image.pixels[:] = b"".join(converted_chunks)


def color_quant(
    image: Image,
    n_colors: int,
    dither: bool = False,
    workers: int = DEFAULT_WORKERS,
) -> None:
    """Quantiza ``image`` usando processos nas fases independentes."""
    if n_colors < 1:
        raise ValueError("A quantidade de cores deve ser maior que zero")
    if workers not in SUPPORTED_WORKERS:
        raise ValueError("A quantidade de processos deve ser 1, 2, 4 ou 8")

    histogram = _build_histogram_multiprocessing(image, workers)
    root = OctreeNode()
    heap = NodeHeap()

    # A combinacao da octree e serial para preservar sua consistencia.
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
        # A difusao de erro depende dos pixels anteriores e permanece serial.
        _error_diffuse(image, palette)
    else:
        _replace_colors_multiprocessing(image, root, workers)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quantiza PPM P6 em paralelo usando multiprocessing."
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
        help=f"numero de processos: 1, 2, 4 ou 8 (padrao: {DEFAULT_WORKERS})",
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

    print(f"Processos: {args.workers}")
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
