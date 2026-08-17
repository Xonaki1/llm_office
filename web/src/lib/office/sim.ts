/**
 * The office simulation.
 *
 * Run events come in; characters walk. The mapping is deliberately literal
 * rather than decorative — it mirrors what the engine actually does:
 *
 *   step.start        the agent reads the shared board, then sits down to work
 *   step.token        it is writing (the text appears over its head)
 *   tool.call         it walks to the station that tool belongs to
 *   artifact.written  a page comes off the printer and lands in the cabinet
 *   step.end          it pins its contribution to the shared board
 *
 * Only one agent ever runs at a time (every preset awaits each turn), so the
 * room has exactly one busy character and the rest carry on with office life.
 *
 * Positions are advanced by `tick` from a requestAnimationFrame loop and are
 * read straight out of these objects by the renderer, which writes them to the
 * DOM without going through React. Discrete changes — a new bubble, a page on
 * the board — set a dirty flag that notifies React at most ten times a second.
 */

import {
  ARCHIVE,
  BOARD,
  BOARD_TILES,
  COOLER,
  DESKS,
  findPath,
  MEETING_SEATS,
  PRINTER,
  stationForTool,
  type Facing,
  type Tile,
} from "@/lib/office/layout";
import { accentFor, paletteFor, type Palette } from "@/components/office/sprites";
import type { RunEvent } from "@/lib/types";

export type Activity = "idle" | "walk" | "work" | "read" | "pin" | "tool" | "talk" | "cheer";

export interface Job {
  to?: Tile;
  facing?: Facing;
  activity: Activity;
  /** How long to hold the activity after arriving. `Infinity` holds until replaced. */
  ms: number;
  sitting?: boolean;
  onArrive?: () => void;
}

export interface Character {
  id: string;
  name: string;
  role: string;
  desk: number;
  palette: Palette;
  accent: string;
  x: number;
  y: number;
  facing: Facing;
  activity: Activity;
  sitting: boolean;
  path: Tile[];
  job: Job | null;
  queue: Job[];
  hold: number;
  bubble: string | null;
  /** Ambient wandering timer, so an idle office still feels alive. */
  nextIdle: number;
}

export interface Effect {
  id: number;
  from: Tile;
  to: Tile;
  t: number;
  duration: number;
  accent: string;
}

export type FeedKind =
  | "start"
  | "reading"
  | "working"
  | "tool"
  | "artifact"
  | "pinned"
  | "done"
  | "failed";

export interface FeedItem {
  id: number;
  kind: FeedKind;
  agent: string;
  /** Tool name, artifact path or error text — whatever the kind implies. */
  detail?: string;
  accent: string;
}

const WALK_TILES_PER_SECOND = 3.4;
const BOARD_READ_MS = 900;
const BOARD_PIN_MS = 700;
const TOOL_MIN_MS = 600;
const BUBBLE_CHARS = 150;

export class OfficeSim {
  readonly characters: Character[] = [];
  private readonly byName = new Map<string, Character>();
  private readonly byId = new Map<string, Character>();

  /** One page per finished step, in order. */
  board: Array<{ accent: string }> = [];
  documents = 0;
  effects: Effect[] = [];
  feed: FeedItem[] = [];
  activeName: string | null = null;
  finished: "success" | "failure" | null = null;

  private streaming = "";
  private nextEffectId = 1;
  private nextFeedId = 1;
  private dirty = false;
  private lastNotify = 0;
  private listeners = new Set<() => void>();

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private markDirty(): void {
    this.dirty = true;
  }

  // --- roster ------------------------------------------------------------

  /** Seat the team before the run starts, so the office is never empty. */
  seed(roster: Array<{ id: string; name: string; role: string }>): void {
    for (const member of roster) this.ensure(member.name, member.role, member.id);
  }

  private ensure(name: string, role: string, id?: string): Character {
    const existing = this.byName.get(name);
    if (existing) {
      if (id && !this.byId.has(id)) this.byId.set(id, existing);
      if (role && !existing.role) existing.role = role;
      return existing;
    }

    const desk = this.characters.length % DESKS.length;
    const slot = DESKS[desk];
    const seat = slot ? slot.seat : { x: 12, y: 5 };
    const character: Character = {
      id: id ?? name,
      name,
      role,
      desk,
      palette: paletteFor(id ?? name),
      accent: accentFor(id ?? name),
      x: seat.x,
      y: seat.y,
      facing: slot ? slot.facing : "down",
      activity: "idle",
      sitting: true,
      path: [],
      job: null,
      queue: [],
      hold: 0,
      bubble: null,
      nextIdle: 4000 + this.characters.length * 2500,
    };
    this.characters.push(character);
    this.byName.set(name, character);
    if (id) this.byId.set(id, character);
    this.markDirty();
    return character;
  }

  private home(character: Character): { tile: Tile; facing: Facing } {
    const slot = DESKS[character.desk];
    if (!slot) return { tile: { x: 12, y: 5 }, facing: "down" };
    return { tile: slot.seat, facing: slot.facing };
  }

  /** A free tile in front of the board, so two agents never overlap there. */
  private boardTile(character: Character): Tile {
    const taken = new Set(
      this.characters
        .filter((other) => other !== character)
        .map((other) => `${Math.round(other.x)},${Math.round(other.y)}`),
    );
    for (const tile of BOARD_TILES) {
      if (!taken.has(`${tile.x},${tile.y}`)) return tile;
    }
    return BOARD.stand;
  }

  // --- events ------------------------------------------------------------

  handle(event: RunEvent): void {
    switch (event.type) {
      case "run.start":
        this.pushFeed("start", "", event.workflow_name, "#8a93a6");
        break;

      case "step.start": {
        const character = this.ensure(event.agent_name, event.role, event.agent_id);
        this.activeName = character.name;
        this.streaming = "";
        character.bubble = null;
        const home = this.home(character);
        // Read the board, then go back and work. This is what the engine does:
        // every prompt is the whole shared transcript.
        this.assign(character, [
          { to: this.boardTile(character), facing: "up", activity: "read", ms: BOARD_READ_MS },
          { to: home.tile, facing: home.facing, activity: "work", ms: Infinity, sitting: true },
        ]);
        this.pushFeed("reading", character.name, undefined, character.accent);
        break;
      }

      case "step.token": {
        const character = this.activeCharacter();
        if (!character) break;
        this.streaming += event.text;
        const text = this.streaming.replace(/\s+/g, " ").trim();
        character.bubble = text.length > BUBBLE_CHARS ? `…${text.slice(-BUBBLE_CHARS)}` : text;
        this.markDirty();
        break;
      }

      case "tool.call": {
        const character = this.ensure(event.agent_name, "");
        const { station } = stationForTool(event.tool);
        const home = this.home(character);
        this.assign(character, [
          { to: station.stand, facing: station.facing, activity: "tool", ms: Infinity },
          { to: home.tile, facing: home.facing, activity: "work", ms: Infinity, sitting: true },
        ]);
        character.bubble = null;
        this.pushFeed("tool", character.name, event.tool, character.accent);
        break;
      }

      case "tool.result": {
        const character = this.byName.get(event.agent_name);
        // Release the hold so the character walks back as soon as the tool
        // actually returned, rather than on a guessed timer.
        if (character && character.activity === "tool") {
          character.hold = Math.min(character.hold, TOOL_MIN_MS);
          if (character.job) character.job.ms = Math.min(character.job.ms, TOOL_MIN_MS);
        }
        break;
      }

      case "artifact.written": {
        const character = this.byName.get(event.agent_name);
        // A tool write comes off the printer; text the engine extracted from a
        // reply (`via: "message"`) leaves from the agent's own desk.
        const source = character
          ? { x: Math.round(character.x), y: Math.round(character.y) }
          : PRINTER.stand;
        this.documents += 1;
        this.effects.push({
          id: this.nextEffectId++,
          from: source,
          to: ARCHIVE.stand,
          t: 0,
          duration: 900,
          accent: character?.accent ?? "#8a93a6",
        });
        this.pushFeed("artifact", event.agent_name, event.path, character?.accent ?? "#8a93a6");
        break;
      }

      case "step.end": {
        const character = this.ensure(event.agent_name, event.role ?? "");
        this.streaming = "";
        character.bubble = null;
        const home = this.home(character);
        this.assign(character, [
          {
            to: this.boardTile(character),
            facing: "up",
            activity: "pin",
            ms: BOARD_PIN_MS,
            onArrive: () => {
              this.board.push({ accent: character.accent });
              this.markDirty();
            },
          },
          { to: home.tile, facing: home.facing, activity: "idle", ms: Infinity, sitting: true },
        ]);
        if (this.activeName === character.name) this.activeName = null;
        this.pushFeed("pinned", character.name, undefined, character.accent);
        break;
      }

      case "run.end":
      case "run.cancelled":
      case "run.error": {
        const success = event.status === "succeeded";
        this.finished = success ? "success" : "failure";
        this.activeName = null;
        for (const character of this.characters) {
          character.bubble = null;
          if (success) {
            // Everyone gathers at the table for the hand-over.
            const seat = MEETING_SEATS[character.desk % MEETING_SEATS.length];
            this.assign(character, [
              {
                to: seat?.tile ?? BOARD.stand,
                facing: seat?.facing ?? "up",
                activity: "cheer",
                ms: Infinity,
              },
            ]);
          } else {
            const home = this.home(character);
            this.assign(character, [
              { to: home.tile, facing: home.facing, activity: "idle", ms: Infinity, sitting: true },
            ]);
          }
        }
        this.pushFeed(success ? "done" : "failed", "", event.error ?? undefined, "#8a93a6");
        break;
      }

      default:
        break;
    }
  }

  /**
   * Apply a whole recorded run at once and land everyone back at their desks.
   *
   * Used when a finished run is opened: the room should already show what the
   * team produced — a full board, a stocked cabinet — rather than mime two
   * minutes of work nobody asked to watch.
   */
  fastForward(events: RunEvent[]): void {
    for (const event of events) this.handle(event);
    this.settle();
  }

  /** Clear everything a run produced, keeping the team in place. */
  reset(): void {
    this.board = [];
    this.documents = 0;
    this.effects = [];
    this.feed = [];
    this.activeName = null;
    this.finished = null;
    this.streaming = "";
    this.settle();
  }

  private settle(): void {
    for (const character of this.characters) {
      const home = this.home(character);
      character.queue = [];
      character.job = null;
      character.path = [];
      character.hold = Infinity;
      character.activity = "idle";
      character.sitting = true;
      character.bubble = null;
      character.x = home.tile.x;
      character.y = home.tile.y;
      character.facing = home.facing;
    }
    this.markDirty();
  }

  private activeCharacter(): Character | null {
    return this.activeName ? (this.byName.get(this.activeName) ?? null) : null;
  }

  private pushFeed(kind: FeedKind, agent: string, detail: string | undefined, accent: string): void {
    this.feed.push({ id: this.nextFeedId++, kind, agent, detail, accent });
    if (this.feed.length > 200) this.feed.splice(0, this.feed.length - 200);
    this.markDirty();
  }

  private assign(character: Character, jobs: Job[]): void {
    character.queue = jobs.slice(1);
    character.job = jobs[0] ?? null;
    character.hold = 0;
    character.path = [];
    if (character.job) this.startJob(character, character.job);
    this.markDirty();
  }

  private startJob(character: Character, job: Job): void {
    const from = { x: Math.round(character.x), y: Math.round(character.y) };
    character.path = job.to ? findPath(from, job.to) : [];
    if (character.path.length > 0) {
      character.activity = "walk";
      character.sitting = false;
    } else {
      this.arrive(character, job);
    }
  }

  private arrive(character: Character, job: Job): void {
    character.activity = job.activity;
    character.sitting = job.sitting === true;
    if (job.facing) character.facing = job.facing;
    character.hold = job.ms;
    job.onArrive?.();
    this.markDirty();
  }

  // --- frame -------------------------------------------------------------

  tick(now: number, deltaMs: number): void {
    const delta = Math.min(deltaMs, 100) / 1000;

    for (const character of this.characters) {
      // A backed-up queue means the run is outrunning the animation: walk
      // faster *and* burn through the holds faster, rather than drifting
      // further behind with every event.
      const rush = character.queue.length > 1 ? 2.2 : 1;
      this.advance(character, delta * rush, deltaMs * rush, now);
    }

    for (const effect of this.effects) effect.t += deltaMs;
    if (this.effects.some((effect) => effect.t >= effect.duration)) {
      this.effects = this.effects.filter((effect) => effect.t < effect.duration);
      this.markDirty();
    }

    if (this.dirty && now - this.lastNotify > 100) {
      this.dirty = false;
      this.lastNotify = now;
      for (const listener of this.listeners) listener();
    }
  }

  private advance(character: Character, delta: number, holdDelta: number, now: number): void {
    if (character.path.length > 0) {
      const target = character.path[0];
      if (!target) {
        character.path = [];
        return;
      }
      const dx = target.x - character.x;
      const dy = target.y - character.y;
      const distance = Math.hypot(dx, dy);
      const step = WALK_TILES_PER_SECOND * delta;

      if (distance <= step) {
        character.x = target.x;
        character.y = target.y;
        character.path.shift();
        if (character.path.length === 0 && character.job) this.arrive(character, character.job);
      } else {
        character.x += (dx / distance) * step;
        character.y += (dy / distance) * step;
        const facing: Facing =
          Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : dy > 0 ? "down" : "up";
        if (facing !== character.facing) {
          character.facing = facing;
        }
      }
      return;
    }

    if (character.hold === Infinity) {
      this.ambient(character, now);
      return;
    }

    if (character.hold > 0) {
      character.hold -= holdDelta;
      if (character.hold > 0) return;
    }

    const next = character.queue.shift();
    if (next) {
      character.job = next;
      this.startJob(character, next);
      return;
    }

    // Nothing queued: settle back at the desk.
    if (character.job && character.activity !== "idle") {
      character.activity = "idle";
      this.markDirty();
    }
  }

  /**
   * Idle office life. Characters with nothing to do occasionally get up for
   * water — it costs nothing and it is the difference between a diagram and a
   * place.
   */
  private ambient(character: Character, now: number): void {
    if (character.activity === "work" || character.activity === "tool") return;
    if (character.name === this.activeName) return;
    if (this.finished === "success") return;
    if (now < character.nextIdle) return;

    character.nextIdle = now + 15000 + Math.random() * 25000;
    if (Math.random() < 0.45) {
      const home = this.home(character);
      this.assign(character, [
        { to: COOLER.stand, facing: COOLER.facing, activity: "talk", ms: 2500 },
        { to: home.tile, facing: home.facing, activity: "idle", ms: Infinity, sitting: true },
      ]);
    }
  }
}
