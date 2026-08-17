/**
 * Pixel art for the office.
 *
 * Characters are written as string art and compiled once into horizontal runs
 * of `<rect>`, which keeps the source editable (you can see the sprite in the
 * code) without paying for one node per pixel. Furniture is drawn directly,
 * because a desk is mostly rectangles anyway.
 *
 * Nothing here holds state or reads the clock: the whole module is pure, so it
 * renders identically on the server and after hydration.
 */

import { memo } from "react";

import { DESKS, GRID_H, GRID_W, TILE, WALL_ROWS, type Rect } from "@/lib/office/layout";
import * as L from "@/lib/office/layout";

// --- string-art compiler -------------------------------------------------

interface Run {
  x: number;
  y: number;
  w: number;
  key: string;
}

/** Merge each row of a sprite into horizontal runs of one colour. */
function compile(rows: string[]): Run[] {
  const runs: Run[] = [];
  rows.forEach((row, y) => {
    let x = 0;
    while (x < row.length) {
      const key = row[x];
      if (key === undefined || key === ".") {
        x += 1;
        continue;
      }
      let width = 1;
      while (row[x + width] === key) width += 1;
      runs.push({ x, y, w: width, key });
      x += width;
    }
  });
  return runs;
}

export type Palette = Record<string, string>;

function Sprite({ runs, palette }: { runs: Run[]; palette: Palette }) {
  return (
    <>
      {runs.map((run, index) => {
        const fill = palette[run.key];
        if (!fill) return null;
        return (
          <rect key={index} x={run.x} y={run.y} width={run.w} height={1} fill={fill} />
        );
      })}
    </>
  );
}

// --- character -----------------------------------------------------------

/**
 * 10x12 body, drawn with the feet on the bottom edge of a tile. Legs live in
 * their own group so CSS can swing them without redrawing the sprite.
 *
 *   H hair  S skin  m mouth  E eye  B shirt  P trousers
 */
const BODY_DOWN = compile([
  "..HHHHHH..",
  ".HHHHHHHH.",
  ".HSSSSSSH.",
  ".HSSSSSSH.",
  "..SESSES..",
  "..SSSSSS..",
  "..SSmmSS..",
  ".SBBBBBBS.",
  "SSBBBBBBSS",
  ".SBBBBBBS.",
  "..BBBBBB..",
  "..PPPPPP..",
]);

const BODY_UP = compile([
  "..HHHHHH..",
  ".HHHHHHHH.",
  ".HHHHHHHH.",
  ".HHHHHHHH.",
  "..HHHHHH..",
  "..SSSSSS..",
  "..SSSSSS..",
  ".SBBBBBBS.",
  "SSBBBBBBSS",
  ".SBBBBBBS.",
  "..BBBBBB..",
  "..PPPPPP..",
]);

const BODY_SIDE = compile([
  "..HHHHHH..",
  ".HHHHHHHH.",
  ".HHSSSSSS.",
  ".HHSESSSS.",
  "..HSSSSSS.",
  "..SSSSSSm.",
  "..SSSSSS..",
  "..BBBBBBS.",
  "..BBBBBBSS",
  "..BBBBBBS.",
  "..BBBBBB..",
  "..PPPPPP..",
]);

/** Deterministic looks, so the same agent is always the same person. */
const SHIRTS = ["#e4572e", "#3d7dd8", "#3aa66b", "#b452cf", "#e0a02e", "#2fb3b3", "#d94f7a", "#6f7ae0"];
const HAIRS = ["#2f2a26", "#5a3b22", "#8c5a2b", "#c9a227", "#7a2f2f", "#4a4a52"];
const SKINS = ["#f3c9a0", "#e0aa7c", "#c08557", "#8d5a34"];

function hash(seed: string): number {
  let value = 0;
  for (let index = 0; index < seed.length; index += 1) {
    value = (value * 31 + seed.charCodeAt(index)) >>> 0;
  }
  return value;
}

export function paletteFor(seed: string): Palette {
  const code = hash(seed);
  const shirt = SHIRTS[code % SHIRTS.length] ?? "#3d7dd8";
  const skin = SKINS[(code >> 3) % SKINS.length] ?? "#f3c9a0";
  return {
    B: shirt,
    b: shade(shirt, -0.25),
    H: HAIRS[(code >> 6) % HAIRS.length] ?? "#2f2a26",
    S: skin,
    m: shade(skin, -0.35),
    E: "#2b2118",
    P: "#3c4250",
    K: "#22262f",
  };
}

/** The accent colour used for this agent outside the sprite (dots, bubbles). */
export function accentFor(seed: string): string {
  return SHIRTS[hash(seed) % SHIRTS.length] ?? "#3d7dd8";
}

function shade(hex: string, amount: number): string {
  const value = parseInt(hex.slice(1), 16);
  const channels = [(value >> 16) & 255, (value >> 8) & 255, value & 255];
  const shifted = channels.map((channel) => {
    const next = amount < 0 ? channel * (1 + amount) : channel + (255 - channel) * amount;
    return Math.max(0, Math.min(255, Math.round(next)));
  });
  return `#${shifted.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/**
 * One character, drawn at the origin of its own group.
 *
 * The wrapper sets `data-facing` and `data-activity`; CSS in globals.css turns
 * those into the walk cycle, the typing hands and the idle bob, so the
 * animation costs no React renders at all.
 */
export function CharacterSprite({ palette }: { palette: Palette }) {
  return (
    <g className="ch">
      <ellipse className="ch-shadow" cx={5} cy={14} rx={4} ry={1.2} fill="#000" opacity={0.18} />

      {/* `ch-sit` carries the sitting offset so it never fights the walk,
          bob and flip transforms, which live on their own groups. */}
      <g className="ch-sit">
        <g className="ch-legs">
          <g className="ch-leg ch-leg-a">
            <rect x={2} y={12} width={2} height={1} fill={palette.P} />
            <rect x={2} y={13} width={2} height={1} fill={palette.K} />
          </g>
          <g className="ch-leg ch-leg-b">
            <rect x={6} y={12} width={2} height={1} fill={palette.P} />
            <rect x={6} y={13} width={2} height={1} fill={palette.K} />
          </g>
        </g>

        <g className="ch-body">
          <g className="ch-face ch-face-down">
            <Sprite runs={BODY_DOWN} palette={palette} />
          </g>
          <g className="ch-face ch-face-up">
            <Sprite runs={BODY_UP} palette={palette} />
          </g>
          <g className="ch-face ch-face-side">
            <Sprite runs={BODY_SIDE} palette={palette} />
          </g>
          {/* Hands, shown only while typing. */}
          <g className="ch-hands">
            <rect x={1} y={10} width={2} height={1} fill={palette.S} />
            <rect x={7} y={10} width={2} height={1} fill={palette.S} />
          </g>
        </g>
      </g>
    </g>
  );
}

// --- furniture -----------------------------------------------------------

const C = {
  floor: "#caa87e",
  floorAlt: "#c5a278",
  plank: "#b99770",
  wall: "#e9e2d5",
  wallLow: "#d6cab5",
  wallLine: "#bfb19a",
  sky: "#8fd6f2",
  skyLow: "#bfe9f8",
  frame: "#8a6a4a",
  deskTop: "#b07d4f",
  deskEdge: "#8a5c39",
  deskLeg: "#6f4626",
  screen: "#2f3644",
  screenOn: "#7fd8ff",
  screenOff: "#4a5567",
  board: "#f7f4ec",
  boardFrame: "#7f8896",
  metal: "#c3c9d2",
  metalDark: "#9aa2ad",
  cabinet: "#9d7b57",
  cabinetDark: "#7d5f40",
  terminal: "#2b3040",
  terminalOn: "#7ce38b",
  water: "#a9dcf0",
  leaf: "#4e8f52",
  leafDark: "#3d7341",
  pot: "#b5673f",
  rug: "#7e9c93",
  rugAlt: "#6d8a81",
  chair: "#4d5566",
  chairDark: "#3b4252",
  paper: "#fffdf7",
  paperLine: "#c9c2b4",
  shadow: "#00000018",
} as const;

function px(rect: Rect, fill: string, key?: string) {
  return (
    <rect
      key={key}
      x={rect.x * TILE}
      y={rect.y * TILE}
      width={rect.w * TILE}
      height={rect.h * TILE}
      fill={fill}
    />
  );
}

/** Floor, walls and windows: everything that never changes. */
export const Room = memo(function Room() {
  // Planks two tiles deep: one seam per tile turns the floor into a barcode.
  const planks = [];
  for (let y = WALL_ROWS.top; y < GRID_H - 1; y += 2) {
    planks.push(
      <rect
        key={`p${y}`}
        x={TILE}
        y={y * TILE}
        width={(GRID_W - 2) * TILE}
        height={TILE * 2}
        fill={(y / 2) % 2 === 0 ? C.floor : C.floorAlt}
      />,
      <rect
        key={`l${y}`}
        x={TILE}
        y={y * TILE + TILE * 2 - 1}
        width={(GRID_W - 2) * TILE}
        height={1}
        fill={C.plank}
      />,
    );
  }

  return (
    <g>
      {/* Floor. */}
      {planks}

      {/* Rug under the meeting area. */}
      {px(L.RUG, C.rug)}
      <rect
        x={L.RUG.x * TILE + 3}
        y={L.RUG.y * TILE + 3}
        width={L.RUG.w * TILE - 6}
        height={L.RUG.h * TILE - 6}
        fill="none"
        stroke={C.rugAlt}
        strokeWidth={2}
      />

      {/* Walls. */}
      {px({ x: 0, y: 0, w: GRID_W, h: WALL_ROWS.top }, C.wall)}
      {px({ x: 0, y: WALL_ROWS.top - 0.375, w: GRID_W, h: 0.375 }, C.wallLow)}
      {px({ x: 0, y: 0, w: 1, h: GRID_H }, C.wallLow)}
      {px({ x: GRID_W - 1, y: 0, w: 1, h: GRID_H }, C.wallLow)}
      {px({ x: 0, y: GRID_H - 1, w: GRID_W, h: 1 }, C.wallLow)}
      <rect x={0} y={WALL_ROWS.top * TILE - 2} width={GRID_W * TILE} height={2} fill={C.wallLine} />

      {/* Windows with a sill and a sky gradient done the pixel way. */}
      {L.WINDOWS.map((window, index) => (
        <g key={index}>
          {px({ ...window, y: window.y }, C.frame)}
          <rect
            x={window.x * TILE + 3}
            y={window.y * TILE + 3}
            width={window.w * TILE - 6}
            height={window.h * TILE - 8}
            fill={C.sky}
          />
          <rect
            x={window.x * TILE + 3}
            y={window.y * TILE + window.h * TILE - 12}
            width={window.w * TILE - 6}
            height={7}
            fill={C.skyLow}
          />
          <rect
            x={window.x * TILE + window.w * TILE * 0.5 - 1}
            y={window.y * TILE + 3}
            width={2}
            height={window.h * TILE - 8}
            fill={C.frame}
          />
        </g>
      ))}
    </g>
  );
});

export const Desks = memo(function Desks({ count }: { count: number }) {
  return (
    <g>
      {DESKS.slice(0, count).map((desk, index) => {
        const x = desk.rect.x * TILE;
        const y = desk.rect.y * TILE;
        const w = desk.rect.w * TILE;
        const chairX = desk.seat.x * TILE;
        // The monitor sits on the end of the desk the chair is on.
        const monitorX = desk.facing === "left" ? x + w - 10 : x + 2;
        return (
          <g key={index}>
            {/* Office chair, drawn under whoever is standing on the seat tile:
                a low seat pad with the backrest on the side away from the desk,
                so it never reads as a second monitor. */}
            <rect x={chairX + 4} y={y + 9} width={8} height={5} fill={C.chair} />
            <rect x={chairX + 6} y={y + 14} width={4} height={2} fill={C.chairDark} />
            <rect
              x={desk.facing === "left" ? chairX + 12 : chairX + 2}
              y={y + 6}
              width={2}
              height={8}
              fill={C.chairDark}
            />

            <rect x={x} y={y + 3} width={w} height={TILE - 5} fill={C.deskTop} />
            <rect x={x} y={y + TILE - 3} width={w} height={3} fill={C.deskEdge} />
            <rect x={x + 1} y={y + TILE} width={2} height={2} fill={C.deskLeg} />
            <rect x={x + w - 3} y={y + TILE} width={2} height={2} fill={C.deskLeg} />

            {/* Monitor, on a small stand. */}
            <rect x={monitorX + 3} y={y + 5} width={3} height={2} fill={C.metalDark} />
            <rect x={monitorX} y={y - 3} width={9} height={9} fill={C.screen} />
            <rect
              className="screen"
              data-desk={index}
              x={monitorX + 1}
              y={y - 2}
              width={7}
              height={6}
              fill={C.screenOff}
            />
            {/* Papers on the desk, so it does not read as an empty plank. */}
            <rect
              x={desk.facing === "left" ? x + 2 : x + w - 7}
              y={y + 7}
              width={5}
              height={4}
              fill={C.paper}
            />
          </g>
        );
      })}
    </g>
  );
});

export const MeetingTable = memo(function MeetingTable() {
  const { x, y, w, h } = L.MEETING_TABLE;
  return (
    <g>
      <rect x={x * TILE} y={y * TILE + 2} width={w * TILE} height={h * TILE - 4} fill={C.deskTop} />
      <rect x={x * TILE} y={y * TILE + h * TILE - 4} width={w * TILE} height={3} fill={C.deskEdge} />
      <rect x={x * TILE + 6} y={y * TILE + 6} width={w * TILE - 12} height={h * TILE - 14} fill={shade(C.deskTop, 0.12)} />
      {/* A couple of mugs, because meetings. */}
      <rect x={x * TILE + 10} y={y * TILE + 8} width={4} height={4} fill={C.paper} />
      <rect x={x * TILE + w * TILE - 14} y={y * TILE + 12} width={4} height={4} fill={C.paper} />
    </g>
  );
});

/**
 * The shared board: one pinned page per finished step.
 *
 * Deliberately not memoised — the page list is mutated in place by the
 * simulation, so a shallow prop comparison would never see a new page.
 */
export function Board({ pages }: { pages: Array<{ accent: string }> }) {
  const { x, y, w, h } = L.BOARD.rect;
  const left = x * TILE;
  const top = y * TILE;
  const shown = pages.slice(-16);

  return (
    <g>
      <rect x={left - 3} y={top + 1} width={w * TILE + 6} height={h * TILE - 1} fill={C.boardFrame} />
      <rect x={left - 1} y={top + 3} width={w * TILE + 2} height={h * TILE - 5} fill={C.board} />
      <rect x={left - 1} y={top + 3} width={w * TILE + 2} height={3} fill={shade(C.boardFrame, 0.25)} />
      {shown.map((page, index) => {
        const column = index % 8;
        const row = Math.floor(index / 8);
        return (
          <g key={index} className="board-page">
            <rect
              x={left + 2 + column * 12}
              y={top + 8 + row * 11}
              width={10}
              height={10}
              fill={C.paper}
              stroke={C.paperLine}
              strokeWidth={0.5}
            />
            <rect x={left + 2 + column * 12} y={top + 8 + row * 11} width={10} height={3} fill={page.accent} />
            <rect x={left + 4 + column * 12} y={top + 13 + row * 11} width={6} height={1} fill={C.paperLine} />
            <rect x={left + 4 + column * 12} y={top + 15 + row * 11} width={4} height={1} fill={C.paperLine} />
          </g>
        );
      })}
    </g>
  );
}

export const Printer = memo(function Printer() {
  const { x, y, w } = L.PRINTER.rect;
  const left = x * TILE;
  const top = y * TILE;
  return (
    <g>
      <rect x={left} y={top + 3} width={w * TILE} height={TILE - 4} fill={C.metal} />
      <rect x={left} y={top + 3} width={w * TILE} height={3} fill={C.metalDark} />
      <rect x={left + 4} y={top + 9} width={w * TILE - 8} height={3} fill={C.screen} />
      <rect className="printer-lamp" x={left + w * TILE - 6} y={top + 5} width={2} height={2} fill={C.terminalOn} />
    </g>
  );
});

/** The filing cabinet. Each written artifact adds a folder. */
export const Archive = memo(function Archive({ documents }: { documents: number }) {
  const { x, y, w } = L.ARCHIVE.rect;
  const left = x * TILE;
  const top = y * TILE;
  const folders = Math.min(documents, 9);
  return (
    <g>
      <rect x={left} y={top - 6} width={w * TILE} height={TILE + 6} fill={C.cabinet} />
      <rect x={left} y={top - 6} width={w * TILE} height={2} fill={C.cabinetDark} />
      {[0, 1].map((row) => (
        <rect
          key={row}
          x={left + 3}
          y={top - 3 + row * 10}
          width={w * TILE - 6}
          height={8}
          fill={C.cabinetDark}
        />
      ))}
      {Array.from({ length: folders }, (_, index) => (
        <rect
          key={index}
          x={left + 5 + (index % 5) * 8}
          y={top - 2 + Math.floor(index / 5) * 10}
          width={6}
          height={5}
          fill={index % 2 === 0 ? "#f2e2b8" : "#e8cf9a"}
        />
      ))}
    </g>
  );
});

/** The internet terminal — where web tools happen. */
export const Terminal = memo(function Terminal() {
  const { x, y, w } = L.TERMINAL.rect;
  const left = x * TILE;
  const top = y * TILE;
  return (
    <g>
      <rect x={left} y={top - 8} width={w * TILE} height={TILE + 8} fill={C.terminal} />
      <rect className="terminal-screen" x={left + 3} y={top - 5} width={w * TILE - 6} height={10} fill={C.screenOff} />
      {[0, 1, 2].map((index) => (
        <rect
          key={index}
          className="terminal-led"
          x={left + 4 + index * 6}
          y={top + 8}
          width={3}
          height={2}
          fill={C.terminalOn}
          opacity={0.5}
        />
      ))}
    </g>
  );
});

export const Cooler = memo(function Cooler() {
  const { x, y } = L.COOLER.rect;
  const left = x * TILE;
  const top = y * TILE;
  return (
    <g>
      <rect x={left + 3} y={top - 8} width={10} height={9} fill={C.water} />
      <rect x={left + 4} y={top + 1} width={8} height={TILE - 1} fill={C.metal} />
      <rect x={left + 5} y={top + 5} width={6} height={3} fill={C.metalDark} />
    </g>
  );
});

export const Plants = memo(function Plants() {
  return (
    <g>
      {L.PLANTS.map((plant, index) => {
        const left = plant.x * TILE;
        const top = plant.y * TILE;
        return (
          <g key={index} className="plant">
            <rect x={left + 5} y={top + 10} width={6} height={6} fill={C.pot} />
            <rect x={left + 3} y={top + 4} width={10} height={6} fill={C.leaf} />
            <rect x={left + 5} y={top + 1} width={6} height={4} fill={C.leafDark} />
          </g>
        );
      })}
    </g>
  );
});
