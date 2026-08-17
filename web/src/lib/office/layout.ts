/**
 * The office floor plan.
 *
 * Everything is measured in tiles of `TILE` art pixels. The scene is drawn into
 * an SVG whose viewBox is exactly the grid, then scaled up by the browser with
 * `shape-rendering: crispEdges`, so the art stays pixel-sharp at any width.
 *
 * The grid is kept small on purpose: fewer, larger tiles means the people are
 * big enough to read at a glance, which is the whole point of the view. The
 * layout is static, which is what lets the simulation route a character with a
 * plain breadth-first search over walkable tiles.
 */

export const TILE = 16;
export const GRID_W = 22;
export const GRID_H = 14;

export const SCENE_W = GRID_W * TILE;
export const SCENE_H = GRID_H * TILE;

export interface Tile {
  x: number;
  y: number;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Where a character can stand while using a piece of furniture. */
export interface Station {
  /** The furniture itself — blocked for walking. */
  rect: Rect;
  /** The floor tile a character occupies while using it. */
  stand: Tile;
  /** Which way the character looks while standing there. */
  facing: Facing;
}

export type Facing = "up" | "down" | "left" | "right";

// --- rooms and furniture -------------------------------------------------

/**
 * The wall band is two tiles tall so the board mounted on it has room for the
 * pages that accumulate during a run. Floor is rows 2..12.
 */
export const WALL_ROWS = { top: 2, bottom: GRID_H - 1 };

export const WINDOWS: Rect[] = [
  { x: 2, y: 0, w: 5, h: 2 },
  { x: 15, y: 0, w: 5, h: 2 },
];

/**
 * The shared board.
 *
 * The engine keeps one transcript every agent appends to and every later agent
 * reads in full (`core/orchestration/state.py`), so a physical board in the
 * middle of the room is not decoration — it is the literal data structure.
 */
export const BOARD: Station = {
  rect: { x: 8, y: 0, w: 6, h: 2 },
  stand: { x: 10, y: 2 },
  facing: "up",
};

/** Extra tiles in front of the board, so two agents never overlap at it. */
export const BOARD_TILES: Tile[] = [
  { x: 9, y: 2 },
  { x: 10, y: 2 },
  { x: 11, y: 2 },
  { x: 12, y: 2 },
];

export const MEETING_TABLE: Rect = { x: 8, y: 7, w: 6, h: 2 };

/** Seats around the meeting table, alternating sides so pairs face each other. */
export const MEETING_SEATS: Array<{ tile: Tile; facing: Facing }> = [
  { tile: { x: 9, y: 6 }, facing: "down" },
  { tile: { x: 12, y: 6 }, facing: "down" },
  { tile: { x: 9, y: 9 }, facing: "up" },
  { tile: { x: 12, y: 9 }, facing: "up" },
  { tile: { x: 10, y: 6 }, facing: "down" },
  { tile: { x: 11, y: 9 }, facing: "up" },
];

export interface DeskSlot {
  /** The desk surface. */
  rect: Rect;
  /** The chair the agent works from. */
  seat: Tile;
  facing: Facing;
}

/**
 * Eight desks along the side walls, filled left-right-left-right so a small
 * team spreads across the room instead of huddling in one corner.
 */
export const DESKS: DeskSlot[] = [4, 6, 8, 10].flatMap((row) => [
  { rect: { x: 1, y: row, w: 2, h: 1 }, seat: { x: 3, y: row }, facing: "left" as const },
  { rect: { x: 18, y: row, w: 2, h: 1 }, seat: { x: 17, y: row }, facing: "right" as const },
]);

/** Stations an agent walks to when it uses a tool. */
export const PRINTER: Station = {
  rect: { x: 3, y: 12, w: 2, h: 1 },
  stand: { x: 3, y: 11 },
  facing: "down",
};

export const ARCHIVE: Station = {
  rect: { x: 6, y: 12, w: 3, h: 1 },
  stand: { x: 7, y: 11 },
  facing: "down",
};

export const TERMINAL: Station = {
  rect: { x: 12, y: 12, w: 3, h: 1 },
  stand: { x: 13, y: 11 },
  facing: "down",
};

export const COOLER: Station = {
  rect: { x: 16, y: 12, w: 1, h: 1 },
  stand: { x: 16, y: 11 },
  facing: "down",
};

export const PLANTS: Tile[] = [
  { x: 1, y: 2 },
  { x: 19, y: 2 },
  { x: 10, y: 12 },
  { x: 19, y: 12 },
];

export const RUG: Rect = { x: 7, y: 6, w: 8, h: 4 };

// --- walkability ---------------------------------------------------------

const BLOCKED_RECTS: Rect[] = [
  // Walls: the top band is two tiles tall, the others one.
  { x: 0, y: 0, w: GRID_W, h: WALL_ROWS.top },
  { x: 0, y: GRID_H - 1, w: GRID_W, h: 1 },
  { x: 0, y: 0, w: 1, h: GRID_H },
  { x: GRID_W - 1, y: 0, w: 1, h: GRID_H },
  // Furniture.
  MEETING_TABLE,
  PRINTER.rect,
  ARCHIVE.rect,
  TERMINAL.rect,
  COOLER.rect,
  ...DESKS.map((desk) => desk.rect),
  ...PLANTS.map((plant) => ({ x: plant.x, y: plant.y, w: 1, h: 1 })),
];

function buildGrid(): boolean[] {
  const walkable = new Array<boolean>(GRID_W * GRID_H).fill(true);
  for (const rect of BLOCKED_RECTS) {
    for (let y = rect.y; y < rect.y + rect.h; y += 1) {
      for (let x = rect.x; x < rect.x + rect.w; x += 1) {
        if (x >= 0 && x < GRID_W && y >= 0 && y < GRID_H) walkable[y * GRID_W + x] = false;
      }
    }
  }
  return walkable;
}

const WALKABLE = buildGrid();

export function isWalkable(x: number, y: number): boolean {
  if (x < 0 || y < 0 || x >= GRID_W || y >= GRID_H) return false;
  return WALKABLE[y * GRID_W + x] === true;
}

export function sameTile(a: Tile, b: Tile): boolean {
  return a.x === b.x && a.y === b.y;
}

const pathCache = new Map<string, Tile[]>();

/**
 * Shortest walk from one tile to another, excluding the starting tile.
 *
 * The grid is 22x14, so a plain BFS is both optimal and instant; results are
 * cached because characters walk the same handful of routes over and over.
 */
export function findPath(from: Tile, to: Tile): Tile[] {
  if (sameTile(from, to)) return [];
  const key = `${from.x},${from.y}>${to.x},${to.y}`;
  const cached = pathCache.get(key);
  if (cached) return cached;

  const start = from.y * GRID_W + from.x;
  const goal = to.y * GRID_W + to.x;
  const previous = new Map<number, number>();
  const queue: number[] = [start];
  const seen = new Set<number>([start]);
  let head = 0;
  let found = false;

  while (head < queue.length) {
    const current = queue[head];
    head += 1;
    if (current === undefined) break;
    if (current === goal) {
      found = true;
      break;
    }
    const cx = current % GRID_W;
    const cy = Math.floor(current / GRID_W);
    // Cardinal moves only: diagonal steps would clip furniture corners.
    const neighbours = [
      { x: cx, y: cy - 1 },
      { x: cx + 1, y: cy },
      { x: cx, y: cy + 1 },
      { x: cx - 1, y: cy },
    ];
    for (const next of neighbours) {
      if (!isWalkable(next.x, next.y)) continue;
      const index = next.y * GRID_W + next.x;
      if (seen.has(index)) continue;
      seen.add(index);
      previous.set(index, current);
      queue.push(index);
    }
  }

  if (!found) {
    // Unreachable target (should not happen with the static map, but a
    // character must never get stuck): stay put rather than teleport.
    pathCache.set(key, []);
    return [];
  }

  const path: Tile[] = [];
  let cursor: number | undefined = goal;
  while (cursor !== undefined && cursor !== start) {
    path.push({ x: cursor % GRID_W, y: Math.floor(cursor / GRID_W) });
    cursor = previous.get(cursor);
  }
  path.reverse();
  pathCache.set(key, path);
  return path;
}

/** The station a tool call sends its agent to. */
export function stationForTool(tool: string): {
  station: Station;
  place: "printer" | "archive" | "terminal" | "desk";
} {
  switch (tool) {
    case "write_artifact":
    case "edit_artifact":
      return { station: PRINTER, place: "printer" };
    case "read_artifact":
    case "list_artifacts":
      return { station: ARCHIVE, place: "archive" };
    case "web_search":
    case "web_fetch":
      return { station: TERMINAL, place: "terminal" };
    default:
      return { station: ARCHIVE, place: "desk" };
  }
}
