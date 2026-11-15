"""Pure-Python implementation of the Mapbox Earcut polygon triangulation algorithm.

Supports simple polygons, concave polygons, collinear points, and polygons with holes.
"""

from __future__ import annotations


class Node:
    __slots__ = ("i", "x", "y", "prev", "next", "z", "prevZ", "nextZ", "steiner")

    def __init__(self, i: int, x: float, y: float):
        self.i = i
        self.x = x
        self.y = y
        self.prev: Node = self
        self.next: Node = self
        self.z: float = 0.0
        self.prevZ: Node | None = None
        self.nextZ: Node | None = None
        self.steiner: bool = False


def earcut(data: list[float], hole_indices: list[int] | None = None, dim: int = 2) -> list[int]:
    """
    Triangulates a polygon given as a flat array of vertex coordinates.
    Returns a list of vertex indices (triplets forming triangles).
    """
    has_holes = bool(hole_indices and len(hole_indices) > 0)
    outer_len = hole_indices[0] * dim if has_holes and hole_indices else len(data)
    outer_node = _create_linked_list(data, 0, outer_len, dim, True)
    triangles: list[int] = []

    if not outer_node or outer_node.next == outer_node.prev:
        return triangles

    if has_holes and hole_indices:
        outer_node = _eliminate_holes(data, hole_indices, outer_node, dim)

    min_x = 0.0
    min_y = 0.0
    inv_size = 0.0

    if len(data) > 80 * dim:
        min_x = data[0]
        min_y = data[1]
        max_x = min_x
        max_y = min_y
        for i in range(dim, outer_len, dim):
            x = data[i]
            y = data[i + 1]
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
        inv_size = max(max_x - min_x, max_y - min_y)
        inv_size = 32767.0 / inv_size if inv_size != 0.0 else 0.0

    _earcut_linked(outer_node, triangles, min_x, min_y, inv_size)
    return triangles


def _create_linked_list(
    data: list[float], start: int, end: int, dim: int, clockwise: bool
) -> Node | None:
    last: Node | None = None
    if clockwise == (_signed_area(data, start, end, dim) > 0):
        for i in range(start, end, dim):
            last = _insert_node(i // dim, data[i], data[i + 1], last)
    else:
        for i in range(end - dim, start - 1, -dim):
            last = _insert_node(i // dim, data[i], data[i + 1], last)

    if last and _equals(last, last.next):
        _remove_node(last)
        last = last.next

    return last


def _filter_points(start: Node, end: Node | None = None) -> Node:
    if not end:
        end = start
    p = start
    while True:
        again = False
        if (
            p != p.next
            and not p.steiner
            and (_equals(p, p.next) or _area(p.prev, p, p.next) == 0.0)
        ):
            _remove_node(p)
            p = end = p.prev
            if p == p.next:
                break
            again = True
        else:
            p = p.next
        if not (again or p != end):
            break
    return end


def _earcut_linked(
    ear: Node | None,
    triangles: list[int],
    min_x: float,
    min_y: float,
    inv_size: float,
    pass_num: int = 0,
):
    if not ear:
        return

    if inv_size != 0.0 and pass_num == 0:
        _index_curve(ear, min_x, min_y, inv_size)

    stop = ear
    while ear.prev != ear.next:
        prev = ear.prev
        nxt = ear.next

        is_an_ear = (
            _is_ear_hashed(ear, min_x, min_y, inv_size)
            if inv_size != 0.0
            else _is_ear(ear)
        )

        if is_an_ear:
            triangles.append(prev.i)
            triangles.append(ear.i)
            triangles.append(nxt.i)

            _remove_node(ear)
            ear = nxt.next
            stop = nxt.next
            continue

        ear = nxt
        if ear == stop:
            if pass_num == 0:
                _earcut_linked(_filter_points(ear), triangles, min_x, min_y, inv_size, 1)
            elif pass_num == 1:
                ear = _cure_local_intersections(_filter_points(ear), triangles)
                _earcut_linked(ear, triangles, min_x, min_y, inv_size, 2)
            elif pass_num == 2:
                _split_earcut(ear, triangles, min_x, min_y, inv_size)
            break


def _is_ear(ear: Node) -> bool:
    a = ear.prev
    b = ear
    c = ear.next

    if _area(a, b, c) >= 0:
        return False

    p = ear.next.next
    while p != ear.prev:
        if (
            _point_in_triangle(a.x, a.y, b.x, b.y, c.x, c.y, p.x, p.y)
            and _area(p.prev, p, p.next) >= 0
        ):
            return False
        p = p.next
    return True


def _is_ear_hashed(ear: Node, min_x: float, min_y: float, inv_size: float) -> bool:
    a = ear.prev
    b = ear
    c = ear.next

    if _area(a, b, c) >= 0:
        return False

    min_tx = min(a.x, b.x, c.x)
    min_ty = min(a.y, b.y, c.y)
    max_tx = max(a.x, b.x, c.x)
    max_ty = max(a.y, b.y, c.y)

    min_z = _z_order(min_tx, min_ty, min_x, min_y, inv_size)
    max_z = _z_order(max_tx, max_ty, min_x, min_y, inv_size)

    p = ear.prevZ
    n = ear.nextZ

    while p and p.z >= min_z and n and n.z <= max_z:
        if (
            p != ear.prev
            and p != ear.next
            and _point_in_triangle(a.x, a.y, b.x, b.y, c.x, c.y, p.x, p.y)
            and _area(p.prev, p, p.next) >= 0
        ):
            return False
        p = p.prevZ

        if (
            n != ear.prev
            and n != ear.next
            and _point_in_triangle(a.x, a.y, b.x, b.y, c.x, c.y, n.x, n.y)
            and _area(n.prev, n, n.next) >= 0
        ):
            return False
        n = n.nextZ

    while p and p.z >= min_z:
        if (
            p != ear.prev
            and p != ear.next
            and _point_in_triangle(a.x, a.y, b.x, b.y, c.x, c.y, p.x, p.y)
            and _area(p.prev, p, p.next) >= 0
        ):
            return False
        p = p.prevZ

    while n and n.z <= max_z:
        if (
            n != ear.prev
            and n != ear.next
            and _point_in_triangle(a.x, a.y, b.x, b.y, c.x, c.y, n.x, n.y)
            and _area(n.prev, n, n.next) >= 0
        ):
            return False
        n = n.nextZ

    return True


def _cure_local_intersections(start: Node, triangles: list[int]) -> Node:
    p = start
    while True:
        a = p.prev
        b = p.next.next
        if (
            not _equals(a, b)
            and _intersects(a, p, p.next, b)
            and _locally_inside(a, b)
            and _locally_inside(b, a)
        ):
            triangles.append(a.i)
            triangles.append(p.i)
            triangles.append(p.next.i)

            _remove_node(p)
            _remove_node(p.next)
            p = start = b
        p = p.next
        if p == start:
            break
    return _filter_points(p)


def _split_earcut(
    start: Node, triangles: list[int], min_x: float, min_y: float, inv_size: float
):
    a = start
    while True:
        b = a.next.next
        while b != a.prev:
            if a.i != b.i and _is_valid_diagonal(a, b):
                c = _split_polygon(a, b)
                a = _filter_points(a, a.next)
                c = _filter_points(c, c.next)
                _earcut_linked(a, triangles, min_x, min_y, inv_size)
                _earcut_linked(c, triangles, min_x, min_y, inv_size)
                return
            b = b.next
        a = a.next
        if a == start:
            break


def _eliminate_holes(
    data: list[float], hole_indices: list[int], outer_node: Node, dim: int
) -> Node:
    queue: list[Node] = []
    len_holes = len(hole_indices)
    for i in range(len_holes):
        start = hole_indices[i] * dim
        end = hole_indices[i + 1] * dim if i < len_holes - 1 else len(data)
        hole_node = _create_linked_list(data, start, end, dim, False)
        if hole_node:
            if hole_node == hole_node.next:
                hole_node.steiner = True
            queue.append(_get_leftmost(hole_node))

    queue.sort(key=lambda n: n.x)

    for hole in queue:
        outer_node = _eliminate_hole(hole, outer_node)

    return outer_node


def _eliminate_hole(hole: Node, outer_node: Node) -> Node:
    bridge = _find_hole_bridge(hole, outer_node)
    if not bridge:
        return outer_node

    bridge2 = _split_polygon(bridge, hole)
    _filter_points(bridge2, bridge2.next)
    return _filter_points(bridge, bridge.next)


def _find_hole_bridge(hole: Node, outer_node: Node) -> Node | None:
    p = outer_node
    hx = hole.x
    hy = hole.y
    qx = -float("inf")
    m: Node | None = None

    while True:
        if hy <= p.y and hy >= p.next.y and p.next.y != p.y:
            x = p.x + (hy - p.y) * (p.next.x - p.x) / (p.next.y - p.y)
            if x <= hx and x > qx:
                qx = x
                m = p if p.x < p.next.x else p.next
        p = p.next
        if p == outer_node:
            break

    if not m:
        return None

    if hx == qx:
        return m

    stop = m
    mx = m.x
    my = m.y
    tan_min = float("inf")

    p = m
    while True:
        if (
            hx >= p.x
            and p.x >= mx
            and hx != p.x
            and _point_in_triangle(
                hx if hy < my else qx, hy, mx, my, qx if hy < my else hx, hy, p.x, p.y
            )
        ):
            tan = abs(hy - p.y) / (hx - p.x)
            if (
                (_locally_inside(p, hole) or _locally_inside(hole, p))
                and (
                    tan < tan_min
                    or (tan == tan_min and (p.x > m.x or (p.x == m.x and _sector_contains_sector(m, p))))
                )
            ):
                m = p
                tan_min = tan
        p = p.next
        if p == stop:
            break

    return m


def _sector_contains_sector(m: Node, p: Node) -> bool:
    return _area(m.prev, m, p.prev) < 0 and _area(p.next, m, m.next) < 0


def _index_curve(start: Node, min_x: float, min_y: float, inv_size: float):
    p = start
    while True:
        if p.z == 0.0:
            p.z = float(_z_order(p.x, p.y, min_x, min_y, inv_size))
        p.prevZ = p.prev
        p.nextZ = p.next
        p = p.next
        if p == start:
            break

    assert p.prevZ is not None
    p.prevZ.nextZ = None
    p.prevZ = None

    _sort_linked(p)


def _sort_linked(head: Node | None) -> Node | None:
    in_size = 1
    while True:
        p = head
        head = None
        tail = None
        num_merges = 0

        while p:
            num_merges += 1
            q = p
            p_size = 0
            for _ in range(in_size):
                p_size += 1
                q = q.nextZ
                if not q:
                    break

            q_size = in_size
            while p_size > 0 or (q_size > 0 and q):
                if p_size == 0:
                    e = q
                    assert q is not None
                    q = q.nextZ
                    q_size -= 1
                elif q_size == 0 or not q:
                    e = p
                    assert p is not None
                    p = p.nextZ
                    p_size -= 1
                elif p.z <= q.z:
                    e = p
                    p = p.nextZ
                    p_size -= 1
                else:
                    e = q
                    q = q.nextZ
                    q_size -= 1

                assert e is not None
                if tail:
                    tail.nextZ = e
                else:
                    head = e
                e.prevZ = tail
                tail = e

            p = q

        assert tail is not None
        tail.nextZ = None
        in_size *= 2

        if num_merges <= 1:
            return head


def _z_order(
    x: float, y: float, min_x: float, min_y: float, inv_size: float
) -> int:
    lx = int((x - min_x) * inv_size)
    ly = int((y - min_y) * inv_size)
    lx = (lx | (lx << 8)) & 0x00FF00FF
    lx = (lx | (lx << 4)) & 0x0F0F0F0F
    lx = (lx | (lx << 2)) & 0x33333333
    lx = (lx | (lx << 1)) & 0x55555555

    ly = (ly | (ly << 8)) & 0x00FF00FF
    ly = (ly | (ly << 4)) & 0x0F0F0F0F
    ly = (ly | (ly << 2)) & 0x33333333
    ly = (ly | (ly << 1)) & 0x55555555

    return lx | (ly << 1)


def _get_leftmost(start: Node) -> Node:
    p = start
    leftmost = start
    while True:
        if p.x < leftmost.x or (p.x == leftmost.x and p.y < leftmost.y):
            leftmost = p
        p = p.next
        if p == start:
            break
    return leftmost


def _point_in_triangle(
    ax: float, ay: float, bx: float, by: float, cx: float, cy: float, px: float, py: float
) -> bool:
    return (
        (cx - px) * (ay - py) >= (ax - px) * (cy - py)
        and (ax - px) * (by - py) >= (bx - px) * (ay - py)
        and (bx - px) * (cy - py) >= (cx - px) * (by - py)
    )


def _is_valid_diagonal(a: Node, b: Node) -> bool:
    return (
        a.next.i != b.i
        and a.prev.i != b.i
        and not _intersects_polygon(a, b)
        and (
            (_locally_inside(a, b) and _locally_inside(b, a) and _middle_inside(a, b))
            and (_area(a.prev, a, a.next) != 0.0 or _area(b.prev, b, b.next) != 0.0)
            or (_equals(a, b) and _area(a.prev, a, a.next) > 0 and _area(b.prev, b, b.next) < 0)
        )
    )


def _area(p: Node, q: Node, r: Node) -> float:
    return (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)


def _equals(p1: Node, p2: Node) -> bool:
    return p1.x == p2.x and p1.y == p2.y


def _intersects(p1: Node, q1: Node, p2: Node, q2: Node) -> bool:
    o1 = _sign(_area(p1, q1, p2))
    o2 = _sign(_area(p1, q1, q2))
    o3 = _sign(_area(p2, q2, p1))
    o4 = _sign(_area(p2, q2, q1))

    if o1 != o2 and o3 != o4:
        return True

    if o1 == 0 and _on_segment(p1, p2, q1):
        return True
    if o2 == 0 and _on_segment(p1, q2, q1):
        return True
    if o3 == 0 and _on_segment(p2, p1, q2):
        return True
    if o4 == 0 and _on_segment(p2, q1, q2):
        return True

    return False


def _on_segment(p: Node, q: Node, r: Node) -> bool:
    return (
        q.x <= max(p.x, r.x)
        and q.x >= min(p.x, r.x)
        and q.y <= max(p.y, r.y)
        and q.y >= min(p.y, r.y)
    )


def _sign(num: float) -> int:
    return 1 if num > 0 else (-1 if num < 0 else 0)


def _intersects_polygon(a: Node, b: Node) -> bool:
    p = a
    while True:
        if (
            p.i != a.i
            and p.next.i != a.i
            and p.i != b.i
            and p.next.i != b.i
            and _intersects(p, p.next, a, b)
        ):
            return True
        p = p.next
        if p == a:
            break
    return False


def _locally_inside(a: Node, b: Node) -> bool:
    if _area(a.prev, a, a.next) < 0:
        return _area(a, b, a.next) >= 0 and _area(a, a.prev, b) >= 0
    return _area(a, b, a.prev) < 0 or _area(a, a.next, b) < 0


def _middle_inside(a: Node, b: Node) -> bool:
    p = a
    inside = False
    px = (a.x + b.x) / 2.0
    py = (a.y + b.y) / 2.0
    while True:
        if (
            ((p.y > py) != (p.next.y > py))
            and p.next.y != p.y
            and (px < (p.next.x - p.x) * (py - p.y) / (p.next.y - p.y) + p.x)
        ):
            inside = not inside
        p = p.next
        if p == a:
            break
    return inside


def _split_polygon(a: Node, b: Node) -> Node:
    a2 = Node(a.i, a.x, a.y)
    b2 = Node(b.i, b.x, b.y)
    an = a.next
    bp = b.prev

    a.next = b
    b.prev = a

    a2.next = an
    an.prev = a2

    b2.next = a2
    a2.prev = b2

    bp.next = b2
    b2.prev = bp

    return b2


def _insert_node(i: int, x: float, y: float, last: Node | None) -> Node:
    p = Node(i, x, y)
    if not last:
        p.prev = p
        p.next = p
    else:
        p.next = last.next
        p.prev = last
        last.next.prev = p
        last.next = p
    return p


def _remove_node(p: Node):
    p.next.prev = p.prev
    p.prev.next = p.next
    if p.prevZ:
        p.prevZ.nextZ = p.nextZ
    if p.nextZ:
        p.nextZ.prevZ = p.prevZ


def _signed_area(data: list[float], start: int, end: int, dim: int) -> float:
    s = 0.0
    j = end - dim
    for i in range(start, end, dim):
        s += (data[j] - data[i]) * (data[i + 1] + data[j + 1])
        j = i
    return s
