#!/usr/bin/env python3
"""Quantizacao de cores PPM (P6) usando octree.

Port direto do algoritmo de ``color_quantization_cedraz/color_quant.c``.
O modulo pode ser importado ou executado pela linha de comando:

    python3 color_quant.py entrada.ppm 16 saida.ppm
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import BinaryIO, Sequence


@dataclass(slots=True)
class Image:
    width: int
    height: int
    pixels: bytearray

    def __post_init__(self) -> None:
        expected = self.width * self.height * 3
        if self.width <= 0 or self.height <= 0:
            raise ValueError("As dimensoes da imagem devem ser positivas")
        if len(self.pixels) != expected:
            raise ValueError(
                f"Dados RGB invalidos: esperados {expected} bytes, "
                f"recebidos {len(self.pixels)}"
            )


@dataclass(slots=True)
class OctreeNode:
    red: int = 0
    green: int = 0
    blue: int = 0
    count: int = 0
    heap_index: int = 0
    child_index: int = 0
    depth: int = 0
    in_heap: bool = False
    parent: OctreeNode | None = None
    children: list[OctreeNode | None] = field(
        default_factory=lambda: [None] * 8
    )
    child_count: int = 0


class NodeHeap:
    """Heap minimo indexado a partir de 1, como no codigo C original."""

    def __init__(self) -> None:
        self.nodes: list[OctreeNode | None] = [None]

    def __len__(self) -> int:
        return len(self.nodes) - 1

    @staticmethod
    def _key(node: OctreeNode) -> tuple[int, int]:
        return node.child_count, node.count >> node.depth

    @classmethod
    def _comes_before(cls, left: OctreeNode, right: OctreeNode) -> bool:
        return cls._key(left) < cls._key(right)

    def _down(self, node: OctreeNode) -> None:
        index = node.heap_index
        size = len(self.nodes)

        while True:
            child = index * 2
            if child >= size:
                break
            if (
                child + 1 < size
                and self._comes_before(self.nodes[child + 1], self.nodes[child])  # type: ignore[arg-type]
            ):
                child += 1

            child_node = self.nodes[child]
            assert child_node is not None
            if not self._comes_before(child_node, node):
                break

            self.nodes[index] = child_node
            child_node.heap_index = index
            index = child

        self.nodes[index] = node
        node.heap_index = index

    def _up(self, node: OctreeNode) -> None:
        index = node.heap_index
        while index > 1:
            parent_index = index // 2
            parent = self.nodes[parent_index]
            assert parent is not None
            if not self._comes_before(node, parent):
                break
            self.nodes[index] = parent
            parent.heap_index = index
            index = parent_index

        self.nodes[index] = node
        node.heap_index = index

    def add(self, node: OctreeNode) -> None:
        if node.in_heap:
            self._down(node)
            self._up(node)
            return

        node.in_heap = True
        node.heap_index = len(self.nodes)
        self.nodes.append(node)
        self._up(node)

    def pop(self) -> OctreeNode:
        if len(self) == 0:
            raise IndexError("Nao e possivel remover de um heap vazio")

        result = self.nodes[1]
        assert result is not None
        last = self.nodes.pop()

        if len(self) > 0:
            assert last is not None
            self.nodes[1] = last
            last.heap_index = 1
            self._down(last)

        result.in_heap = False
        result.heap_index = 0
        return result

    def palette(self) -> list[OctreeNode]:
        return [node for node in self.nodes[1:] if node is not None]


def _read_token(stream: BinaryIO) -> bytes:
    """Le um token de um PPM, ignorando espacos e comentarios."""
    token = bytearray()

    while True:
        char = stream.read(1)
        if not char:
            raise ValueError("Cabecalho PPM incompleto")
        if char == b"#":
            stream.readline()
        elif not char.isspace():
            token.extend(char)
            break

    while True:
        char = stream.read(1)
        if not char or char.isspace():
            break
        if char == b"#":
            stream.readline()
            break
        token.extend(char)

    return bytes(token)


def read_ppm(filename: str | Path) -> Image:
    path = Path(filename)
    with path.open("rb") as stream:
        if _read_token(stream) != b"P6":
            raise ValueError(f"{path} nao e uma imagem PPM binaria (P6)")

        try:
            width = int(_read_token(stream))
            height = int(_read_token(stream))
            max_value = int(_read_token(stream))
        except ValueError as error:
            raise ValueError(f"Cabecalho PPM invalido em {path}") from error

        if max_value != 255:
            raise ValueError(
                f"Valor maximo PPM nao suportado: {max_value}; esperado 255"
            )

        expected = width * height * 3
        pixels = bytearray(stream.read(expected))
        if len(pixels) != expected:
            raise ValueError(
                f"Imagem PPM truncada: esperados {expected} bytes, "
                f"recebidos {len(pixels)}"
            )

    return Image(width, height, pixels)


def write_ppm(image: Image, filename: str | Path) -> None:
    path = Path(filename)
    with path.open("wb") as stream:
        stream.write(f"P6\n{image.width} {image.height}\n255\n".encode("ascii"))
        stream.write(image.pixels)


def _child_index(red: int, green: int, blue: int, bit: int) -> int:
    return (
        (1 if green & bit else 0) * 4
        + (1 if red & bit else 0) * 2
        + (1 if blue & bit else 0)
    )


def node_insert(root: OctreeNode, red: int, green: int, blue: int) -> OctreeNode:
    node = root
    for depth, bit in enumerate((128, 64, 32, 16, 8, 4, 2), start=1):
        index = _child_index(red, green, blue, bit)
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

    node.red += red
    node.green += green
    node.blue += blue
    node.count += 1
    return node


def node_fold(node: OctreeNode) -> OctreeNode:
    if node.child_count:
        raise ValueError("Somente folhas da octree podem ser agrupadas")
    parent = node.parent
    if parent is None:
        raise ValueError("A raiz da octree nao pode ser agrupada")

    parent.count += node.count
    parent.red += node.red
    parent.green += node.green
    parent.blue += node.blue
    parent.child_count -= 1
    parent.children[node.child_index] = None
    return parent


def _find_palette_node(root: OctreeNode, red: int, green: int, blue: int) -> OctreeNode:
    node = root
    for bit in (128, 64, 32, 16, 8, 4, 2, 1):
        child = node.children[_child_index(red, green, blue, bit)]
        if child is None:
            break
        node = child
    return node


def _replace_colors(image: Image, root: OctreeNode) -> None:
    pixels = image.pixels
    for offset in range(0, len(pixels), 3):
        node = _find_palette_node(
            root, pixels[offset], pixels[offset + 1], pixels[offset + 2]
        )
        pixels[offset] = node.red
        pixels[offset + 1] = node.green
        pixels[offset + 2] = node.blue


def _error_diffuse(image: Image, palette: Sequence[OctreeNode]) -> None:
    width = image.width
    values = [channel * 15 for channel in image.pixels]
    weights = ((1, 0, 7), (0, 1, 5), (1, 1, 2), (-1, 1, 1))

    def nearest(red: int, green: int, blue: int) -> OctreeNode:
        return min(
            palette,
            key=lambda node: (
                3 * abs(node.red - red)
                + 5 * abs(node.green - green)
                + 2 * abs(node.blue - blue)
            ),
        )

    for row in range(image.height):
        for column in range(width):
            offset = 3 * (row * width + column)
            # int(a / b) reproduz a divisao inteira do C (trunca em direcao
            # a zero), inclusive quando o erro acumulado e negativo.
            current = [
                max(0, min(255, int(values[offset + channel] / 15)))
                for channel in range(3)
            ]
            node = nearest(*current)
            error = (
                current[0] - node.red,
                current[1] - node.green,
                current[2] - node.blue,
            )

            image.pixels[offset : offset + 3] = bytes(
                (node.red, node.green, node.blue)
            )

            for column_delta, row_delta, weight in weights:
                target_column = column + column_delta
                target_row = row + row_delta
                if not (0 <= target_column < width and target_row < image.height):
                    continue
                target = 3 * (target_row * width + target_column)
                for channel in range(3):
                    values[target + channel] += error[channel] * weight


def color_quant(image: Image, n_colors: int, dither: bool = False) -> None:
    """Quantiza ``image`` in-place para no maximo ``n_colors`` cores."""
    if n_colors < 1:
        raise ValueError("A quantidade de cores deve ser maior que zero")

    root = OctreeNode()
    heap = NodeHeap()
    pixels = image.pixels

    for offset in range(0, len(pixels), 3):
        leaf = node_insert(
            root, pixels[offset], pixels[offset + 1], pixels[offset + 2]
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
        _error_diffuse(image, palette)
    else:
        _replace_colors(image, root)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quantiza uma imagem PPM P6 usando uma octree."
    )
    parser.add_argument("input", type=Path, help="imagem PPM P6 de entrada")
    parser.add_argument("colors", type=int, help="quantidade maxima de cores")
    parser.add_argument("output", type=Path, help="imagem PPM P6 de saida")
    parser.add_argument(
        "--dither",
        action="store_true",
        help="aplica difusao de erro na imagem resultante",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        image = read_ppm(args.input)
        start = perf_counter()
        color_quant(image, args.colors, args.dither)
        elapsed = perf_counter() - start
        write_ppm(image, args.output)
    except (OSError, ValueError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1

    print(f"Tempo de execucao da quantizacao: {elapsed:.9f} segundos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
