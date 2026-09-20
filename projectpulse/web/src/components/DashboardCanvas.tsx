/*
  The canvas: a Program or Project dashboard, built from tiles the person
  chose - `Layout_Program` images 17-24. One dashboard per `(scopeType,
  scopeId)` (see `app/dashboard/service.py`), so this component is the same
  for both levels, parameterised by scope.
*/
import { useEffect, useRef, useState } from "react";
import { WidthProvider, ReactGridLayout as RawGridLayout } from "react-grid-layout/legacy";
import type { Layout, LayoutItem } from "react-grid-layout/legacy";
import "react-grid-layout/css/styles.css";
import {
  load,
  send,
  withProject,
  type ApiProblem,
  type CatalogueBundle,
  type DashboardOut,
  type DashboardScope,
  type TileOut,
} from "../api";
import { Page, Problem } from "./Shell";
import { CustomChartTile, TILE_REGISTRY } from "../tileRegistry";
import { AddTilesModal } from "./AddTilesModal";
import { NewDashboardModal } from "./NewDashboardModal";
import { EditTileModal } from "./EditTileModal";
import { CustomTileModal } from "./CustomTileModal";

const GridLayout = WidthProvider(RawGridLayout);
const GRID_COLS = 12;
const ROW_HEIGHT = 32;

function toLayoutItem(tile: TileOut): LayoutItem {
  return { i: String(tile.id), x: tile.x, y: tile.y, w: tile.w, h: tile.h };
}

/* Everything this project has, one click away - the point being made visible:
   entering a project is entering a real workspace, not just this one canvas.
   Every link carries the project forward via `withProject` (the same
   ambient-selection mechanism the rail's own `ProjectSwitcher` sets), so
   Schedule/Insight/Risk/Team/Calc/Reports open already scoped to it. */
const PROJECT_LINKS = [
  { href: "/project/dashboard", label: "Dashboard" },
  { href: "/gantt", label: "Schedule" },
  { href: "/insight", label: "Insight" },
  { href: "/traceability", label: "Trace" },
  { href: "/risk", label: "Risk" },
  { href: "/team", label: "Team" },
  { href: "/reports", label: "Reports" },
] as const;

function ProjectSubNav() {
  return (
    <nav className="mb-4 flex flex-wrap gap-1.5" aria-label="This project">
      {PROJECT_LINKS.map((link) => {
        const active = link.href === "/project/dashboard";
        return (
          <a
            key={link.href}
            href={withProject(link.href)}
            aria-current={active ? "page" : undefined}
            className={`rounded-md border px-2.5 py-1 text-[12px] font-semibold no-underline ${
              active
                ? "border-navy bg-navy text-surface"
                : "border-rule bg-surface text-ink hover:bg-bg"
            }`}
          >
            {link.label}
          </a>
        );
      })}
    </nav>
  );
}

function TileMenu({
  onEdit,
  onDuplicate,
  onDelete,
}: {
  onEdit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="cursor-pointer rounded border-0 bg-transparent px-1.5 py-0.5 text-[14px] leading-none text-ink-3 hover:text-ink"
        aria-label="Tile options"
      >
        &#8942;
      </button>
      {open && (
        <div
          className="absolute right-0 z-10 mt-1 min-w-[120px] rounded-md border border-rule bg-surface py-1 shadow-lg"
          onMouseLeave={() => setOpen(false)}
        >
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onEdit();
            }}
            className="block w-full cursor-pointer border-0 bg-transparent px-3 py-1.5 text-left text-[12.5px] text-ink hover:bg-bg"
          >
            Edit Title
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onDuplicate();
            }}
            className="block w-full cursor-pointer border-0 bg-transparent px-3 py-1.5 text-left text-[12.5px] text-ink hover:bg-bg"
          >
            Duplicate Tile
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
            className="block w-full cursor-pointer border-0 bg-transparent px-3 py-1.5 text-left text-[12.5px] text-red hover:bg-bg"
          >
            Delete Tile
          </button>
        </div>
      )}
    </div>
  );
}

export function DashboardCanvas({
  scopeType,
  scopeId,
  title,
  scopeLabel,
}: {
  scopeType: DashboardScope;
  scopeId: string;
  title: string;
  scopeLabel?: string;
}) {
  const [dashboard, setDashboard] = useState<DashboardOut | null>(null);
  const [catalogue, setCatalogue] = useState<CatalogueBundle | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  // On by default - a fresh dashboard should feel like a canvas immediately,
  // not require finding a toggle first. The button becomes "Lock Layout"
  // once it's already on, for the person who wants to stop bumping tiles.
  const [dragMode, setDragMode] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [editingTile, setEditingTile] = useState<TileOut | null>(null);
  const [seeding, setSeeding] = useState(false);
  const pending = useRef<Map<number, LayoutItem>>(new Map());
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const query = `scope_type=${scopeType}&scope_id=${encodeURIComponent(scopeId)}`;

  function reload() {
    load<DashboardOut>(`/api/dashboards?${query}`).then(setDashboard, setProblem);
  }

  useEffect(() => {
    reload();
    load<CatalogueBundle>("/api/dashboard/catalogue").then(setCatalogue, () => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeType, scopeId]);

  function flushLayout() {
    if (flushTimer.current) clearTimeout(flushTimer.current);
    flushTimer.current = setTimeout(() => {
      const changes = Array.from(pending.current.entries());
      pending.current.clear();
      for (const [id, item] of changes) {
        send(`/api/dashboards/tiles/${id}`, "PATCH", {
          x: item.x, y: item.y, w: item.w, h: item.h,
        }).catch(() => {});
      }
    }, 400);
  }

  function onLayoutChange(layout: Layout) {
    if (!dashboard) return;
    for (const item of layout) {
      const tile = dashboard.tiles.find((t) => String(t.id) === item.i);
      if (tile && (tile.x !== item.x || tile.y !== item.y || tile.w !== item.w || tile.h !== item.h)) {
        pending.current.set(tile.id, item);
      }
    }
    if (pending.current.size > 0) flushLayout();
  }

  async function deleteTile(id: number) {
    await send(`/api/dashboards/tiles/${id}`, "DELETE");
    reload();
  }

  async function duplicateTile(id: number) {
    await send(`/api/dashboards/tiles/${id}/duplicate`, "POST");
    reload();
  }

  /* "Default setup": the standard board for this level, in one press.

     Which tiles that is lives in `app/dashboard/catalogue.py`
     (`DEFAULT_TEMPLATE`), not here - the program and project defaults are
     designed as a pair against how the two levels actually relate, and a copy
     of that decision in the browser would drift from it.

     It **replaces** the current board, like every other "New Dashboard" path,
     so it confirms first - but only when there is something to lose. A person
     pressing this on the empty canvas is asking for exactly what it does, and
     a prompt there is a dialog with no decision in it. */
  async function applyDefault() {
    if (
      dashboard &&
      dashboard.tiles.length > 0 &&
      !window.confirm(
        `Replace the ${dashboard.tiles.length} tile(s) on this dashboard with the default ${scopeType} setup?`,
      )
    ) {
      return;
    }
    setSeeding(true);
    try {
      await send(`/api/dashboards/default?${query}`, "POST");
      reload();
    } finally {
      setSeeding(false);
    }
  }

  if (problem) {
    return (
      <Page current="/programs" title={title} subtitle={problem.title}>
        <Problem {...problem} />
      </Page>
    );
  }
  if (!dashboard) {
    return (
      <Page current="/programs" title={title} subtitle="Loading..." children={null} />
    );
  }

  const labelFor = (key: string) =>
    key.startsWith("custom:")
      ? "Custom Tile"
      : (catalogue?.tiles.find((t) => t.key === key)?.label ?? key);

  return (
    <Page
      current="/programs"
      title={title}
      scope={scopeLabel}
      wide
      action={
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={applyDefault}
            disabled={seeding}
            title={
              dashboard.tiles.length === 0
                ? "Fill this dashboard with the standard tiles for this level"
                : "Replace this dashboard with the standard tiles for this level"
            }
            className="cursor-pointer rounded-md border border-navy bg-navy px-2.5 py-1 text-[12px] font-semibold text-surface hover:opacity-90 disabled:opacity-50"
          >
            {seeding ? "Setting up..." : "Default setup"}
          </button>
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="cursor-pointer rounded-md border border-rule bg-surface px-2.5 py-1 text-[12px] font-semibold text-ink hover:bg-bg"
          >
            + Add Tiles
          </button>
          <button
            type="button"
            onClick={() => setNewOpen(true)}
            className="cursor-pointer rounded-md border border-rule bg-surface px-2.5 py-1 text-[12px] font-semibold text-ink hover:bg-bg"
          >
            New Dashboard
          </button>
          <button
            type="button"
            onClick={() => setCustomOpen(true)}
            className="cursor-pointer rounded-md border border-purple/50 bg-purple/10 px-2.5 py-1 text-[12px] font-semibold text-purple hover:bg-purple/20"
          >
            Custom Tile
          </button>
          <button
            type="button"
            onClick={() => setDragMode((d) => !d)}
            title={dragMode ? "Tiles can be dragged and resized" : "Layout is locked"}
            className={`cursor-pointer rounded-md border px-2.5 py-1 text-[12px] font-semibold ${
              dragMode ? "border-navy bg-navy text-surface" : "border-rule bg-surface text-ink hover:bg-bg"
            }`}
          >
            {dragMode ? "Drag & Resize: On" : "Lock Layout"}
          </button>
        </div>
      }
    >
      {scopeType === "project" && <ProjectSubNav />}
      {dashboard.tiles.length === 0 ? (
        <div className="flex min-h-[300px] flex-col items-center justify-center rounded-lg border border-dashed border-rule px-6 text-center">
          <p className="m-0 text-[13px] text-ink-3">
            This dashboard is empty. Start from the default {scopeType} setup, or
            add tiles yourself.
          </p>
          <div className="mt-3 flex flex-wrap justify-center gap-2">
            <button
              type="button"
              onClick={applyDefault}
              disabled={seeding}
              className="cursor-pointer rounded-md border border-navy bg-navy px-3 py-1.5 text-[12.5px] font-semibold text-surface hover:opacity-90 disabled:opacity-50"
            >
              {seeding ? "Setting up..." : `Default ${scopeType} setup`}
            </button>
            <button
              type="button"
              onClick={() => setAddOpen(true)}
              className="cursor-pointer rounded-md border border-rule bg-surface px-3 py-1.5 text-[12.5px] font-semibold text-ink hover:bg-bg"
            >
              + Add Tiles
            </button>
          </div>
        </div>
      ) : (
        <GridLayout
          className="layout"
          cols={GRID_COLS}
          rowHeight={ROW_HEIGHT}
          layout={dashboard.tiles.map(toLayoutItem)}
          isDraggable={dragMode}
          isResizable={dragMode}
          draggableCancel=".tile-drag-cancel"
          onLayoutChange={onLayoutChange}
          margin={[14, 14] as const}
        >
          {dashboard.tiles.map((tile) => {
            const Tile = tile.tile_key.startsWith("custom:")
              ? CustomChartTile
              : TILE_REGISTRY[tile.tile_key];
            return (
              <div
                key={String(tile.id)}
                className={`group rounded-lg border border-rule bg-surface p-3.5 ${dragMode ? "cursor-move" : ""}`}
              >
                <div className="mb-2.5 flex items-start justify-between gap-2">
                  <div className="text-[11px] font-bold tracking-[0.07em] text-ink-3 uppercase">
                    {tile.settings?.title ? String(tile.settings.title) : labelFor(tile.tile_key)}
                  </div>
                  {dragMode && (
                    <span
                      className="mr-auto ml-2 text-[10px] tracking-[0.06em] text-ink-3 opacity-0 group-hover:opacity-100"
                      aria-hidden
                    >
                      &#10021; drag
                    </span>
                  )}
                  <div className="tile-drag-cancel shrink-0">
                    <TileMenu
                      onEdit={() => setEditingTile(tile)}
                      onDuplicate={() => duplicateTile(tile.id)}
                      onDelete={() => deleteTile(tile.id)}
                    />
                  </div>
                </div>
                <div className="overflow-auto" style={{ maxHeight: "calc(100% - 28px)" }}>
                  {Tile ? (
                    <Tile
                      scopeType={scopeType}
                      scopeId={scopeId}
                      tileKey={tile.tile_key}
                      settings={tile.settings}
                    />
                  ) : (
                    <p className="m-0 text-[12px] text-ink-3">Unknown tile: {tile.tile_key}</p>
                  )}
                </div>
              </div>
            );
          })}
        </GridLayout>
      )}

      {editingTile && (
        <EditTileModal
          tile={editingTile}
          defaultLabel={labelFor(editingTile.tile_key)}
          onClose={() => setEditingTile(null)}
          onSaved={() => {
            setEditingTile(null);
            reload();
          }}
        />
      )}

      {addOpen && catalogue && (
        <AddTilesModal
          catalogue={catalogue}
          scopeType={scopeType}
          scopeId={scopeId}
          existingTiles={dashboard.tiles}
          onClose={() => setAddOpen(false)}
          onAdded={() => {
            setAddOpen(false);
            reload();
          }}
        />
      )}

      {newOpen && catalogue && (
        <NewDashboardModal
          catalogue={catalogue}
          scopeType={scopeType}
          scopeId={scopeId}
          onClose={() => setNewOpen(false)}
          onCreated={() => {
            setNewOpen(false);
            reload();
          }}
        />
      )}

      {customOpen && (
        <CustomTileModal
          scopeType={scopeType}
          scopeId={scopeId}
          existingTiles={dashboard.tiles}
          onClose={() => setCustomOpen(false)}
          onAdded={() => {
            setCustomOpen(false);
            reload();
          }}
        />
      )}
    </Page>
  );
}
