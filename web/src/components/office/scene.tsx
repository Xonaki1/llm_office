"use client";

import { memo, useEffect, useRef } from "react";

import {
  ARCHIVE,
  GRID_H,
  GRID_W,
  PRINTER,
  SCENE_H,
  SCENE_W,
  TERMINAL,
  TILE,
} from "@/lib/office/layout";
import type { OfficeSim } from "@/lib/office/sim";
import { useSimVersion } from "@/lib/office/use-sim";
import {
  Archive,
  Board,
  CharacterSprite,
  Cooler,
  Desks,
  MeetingTable,
  Plants,
  Printer,
  Room,
  Terminal,
  type Palette,
} from "@/components/office/sprites";

/** The sprite never changes once mounted; only its transform does. */
const Body = memo(function Body({ palette }: { palette: Palette }) {
  return <CharacterSprite palette={palette} />;
});

export function OfficeScene({ sim, className }: { sim: OfficeSim; className?: string }) {
  useSimVersion(sim);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const characterRefs = useRef(new Map<string, SVGGElement>());
  const labelRefs = useRef(new Map<string, HTMLDivElement>());
  const effectRefs = useRef(new Map<number, SVGGElement>());
  const screenRefs = useRef(new Map<number, SVGRectElement>());
  const stationRefs = useRef(new Map<string, SVGGElement>());

  useEffect(() => {
    let frame = 0;
    let previous = performance.now();

    const loop = (now: number) => {
      const delta = now - previous;
      previous = now;
      sim.tick(now, delta);

      const busy = { printer: false, archive: false, terminal: false };

      for (const character of sim.characters) {
        const node = characterRefs.current.get(character.id);
        if (node) {
          // The sprite is 10px wide and 14px tall inside a 16px tile.
          const x = character.x * TILE + (TILE - 10) / 2;
          const y = character.y * TILE + TILE - 14;
          node.setAttribute("transform", `translate(${x.toFixed(2)} ${y.toFixed(2)})`);
          node.dataset.facing = character.facing;
          node.dataset.activity = character.activity;
          node.dataset.sitting = String(character.sitting);
          node.dataset.active = String(character.name === sim.activeName);
        }

        const label = labelRefs.current.get(character.id);
        if (label) {
          label.style.left = `${((character.x * TILE + TILE / 2) / SCENE_W) * 100}%`;
          label.style.top = `${((character.y * TILE + TILE - 15) / SCENE_H) * 100}%`;
        }

        if (character.activity === "tool") {
          const tile = { x: Math.round(character.x), y: Math.round(character.y) };
          if (tile.x === PRINTER.stand.x && tile.y === PRINTER.stand.y) busy.printer = true;
          if (tile.x === ARCHIVE.stand.x && tile.y === ARCHIVE.stand.y) busy.archive = true;
          if (tile.x === TERMINAL.stand.x && tile.y === TERMINAL.stand.y) busy.terminal = true;
        }

        let screen = screenRefs.current.get(character.desk);
        if (!screen) {
          screen =
            svgRef.current?.querySelector<SVGRectElement>(
              `.screen[data-desk="${character.desk}"]`,
            ) ?? undefined;
          if (screen) screenRefs.current.set(character.desk, screen);
        }
        if (screen) screen.dataset.active = String(character.activity === "work");
      }

      for (const [name, node] of stationRefs.current) {
        node.dataset.busy = String(busy[name as keyof typeof busy] === true);
      }

      for (const effect of sim.effects) {
        const node = effectRefs.current.get(effect.id);
        if (!node) continue;
        const progress = Math.min(1, effect.t / effect.duration);
        const x = effect.from.x + (effect.to.x - effect.from.x) * progress;
        const y =
          effect.from.y +
          (effect.to.y - effect.from.y) * progress -
          // A little arc, so the page flies rather than slides.
          Math.sin(progress * Math.PI) * 2;
        node.setAttribute(
          "transform",
          `translate(${(x * TILE + 4).toFixed(2)} ${(y * TILE + 2).toFixed(2)})`,
        );
        node.style.opacity = String(1 - Math.max(0, progress - 0.75) * 4);
      }

      frame = requestAnimationFrame(loop);
    };

    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [sim]);

  return (
    <div className={className}>
      <div className="relative w-full" style={{ aspectRatio: `${GRID_W} / ${GRID_H}` }}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${SCENE_W} ${SCENE_H}`}
          className="office-frame absolute inset-0 h-full w-full"
          shapeRendering="crispEdges"
          role="img"
          aria-label="Office"
        >
          <Room />
          <Board pages={sim.board} />
          <MeetingTable />
          <Desks count={Math.max(sim.characters.length, 2)} />

          <g
            ref={(node) => {
              if (node) stationRefs.current.set("printer", node);
            }}
            className="station"
          >
            <Printer />
          </g>
          <g
            ref={(node) => {
              if (node) stationRefs.current.set("archive", node);
            }}
            className="station"
          >
            <Archive documents={sim.documents} />
          </g>
          <g
            ref={(node) => {
              if (node) stationRefs.current.set("terminal", node);
            }}
            className="station"
          >
            <Terminal />
          </g>
          <Cooler />
          <Plants />

          {sim.characters.map((character) => (
            <g
              key={character.id}
              ref={(node) => {
                if (node) characterRefs.current.set(character.id, node);
                else characterRefs.current.delete(character.id);
              }}
              data-facing={character.facing}
              data-activity={character.activity}
              data-sitting={String(character.sitting)}
              transform={`translate(${character.x * TILE + 3} ${character.y * TILE + 2})`}
            >
              <Body palette={character.palette} />
            </g>
          ))}

          {sim.effects.map((effect) => (
            <g
              key={effect.id}
              ref={(node) => {
                if (node) effectRefs.current.set(effect.id, node);
                else effectRefs.current.delete(effect.id);
              }}
            >
              <rect width={7} height={9} fill="#fffdf7" stroke="#c9c2b4" strokeWidth={0.5} />
              <rect width={7} height={2} fill={effect.accent} />
            </g>
          ))}
        </svg>

        {/* Names and speech live in HTML so the text stays crisp and selectable. */}
        <div className="pointer-events-none absolute inset-0">
          {sim.characters.map((character) => (
            <div
              key={character.id}
              ref={(node) => {
                if (node) labelRefs.current.set(character.id, node);
                else labelRefs.current.delete(character.id);
              }}
              className="absolute -translate-x-1/2 -translate-y-full"
              style={{
                left: `${((character.x * TILE + TILE / 2) / SCENE_W) * 100}%`,
                top: `${((character.y * TILE + TILE - 15) / SCENE_H) * 100}%`,
              }}
            >
              {character.bubble && (
                <div className="office-bubble" style={{ borderColor: character.accent }}>
                  {character.bubble}
                </div>
              )}
              <div className="office-nametag" style={{ background: character.accent }}>
                {character.name}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
