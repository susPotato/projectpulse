/*
  The schedule chart, shared by the Schedule tab and the Calculation tab.

  One component rather than two copies: the Calculation tab embeds the same
  chart it explains, so a reader can look at the bar and then read the
  arithmetic that produced it without switching pages. Two implementations
  would eventually draw two different pictures of one projection.

  Layout: task labels are HTML in a fixed left column, the plot is SVG in a
  horizontally scrolling right column. An earlier version drew the labels
  inside the SVG, which meant they scrolled away with the plot and had to be
  truncated to fit a gutter - both fixed by getting them out of the drawing.

  Computes nothing. Every date arrives already decided by project_schedule().
*/
(function (global) {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";

  //: Row geometry. Mirrored in CSS (--g-row) so the HTML labels and the SVG
  //: rows stay aligned - if these drift, every label points at the wrong bar.
  var ROW = 30;
  var GROUP = 26;
  var BAR_H = 13;
  //: Horizontal density. Below about 3px/day a two-week task is unreadable.
  var PX_PER_DAY = 4.2;
  var MIN_PLOT = 560;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function svg(tag, attrs) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) {
      node.setAttribute(k, attrs[k]);
    });
    return node;
  }

  function svgText(attrs, content) {
    var node = svg("text", attrs);
    node.textContent = content;
    return node;
  }

  function day(iso) {
    if (!iso) return null;
    var p = String(iso).slice(0, 10).split("-");
    return Date.UTC(+p[0], +p[1] - 1, +p[2]) / 86400000;
  }

  function dash(v) { return v === null || v === undefined ? "-" : v; }

  /* Group rows under their milestone, keeping unassigned tasks last rather
     than inventing a bucket that looks like a real milestone. */
  /* A row that can be placed on a time axis. Neither date means there is no
     position to place it at, and the previous behaviour - an empty row, then
     the next one - was not neutral: a Jira export whose backlog carries no
     dates drew 170 blank rows around 20 real ones, and the 4 tasks that were
     genuinely overdue became impossible to find. Blank rows do not read as
     "no date"; they read as a broken chart.

     Left off the plot and counted in the legend instead, which is the same
     bargain the rest of this product makes: say what could not be used rather
     than rendering an absence as if it were a value. Nothing is hidden - the
     count is stated, and every one of these rows is on the Insight page, in
     the findings and in the report. */
  function datable(row) {
    return !!(row.start || row.planned_end);
  }

  /* Open, and past the due date it gives itself.

     The one lateness claim available to a source with no dependency graph.
     `propagated_days` - the red "beyond the plan" segment - is a forward-pass
     result over a DAG, so on an export with no links it is 0 on every row and
     nothing on the chart is ever red. That is correct and it is also useless:
     four tasks thirteen days past their due date drew as ordinary blue bars.

     Deliberately a *different* mark from the overrun, because it is a different
     claim. Overrun is "the chain implies this cannot land on time". This is
     "the date named has passed and nobody closed it", which needs no chain and
     no baseline - only the row and the scan date. Conflating them would let a
     project with no graph appear to have one. */
  function lateAsAt(row, scan) {
    if (scan === null) return null;
    if (row.status === "DONE" || row.status === "DROPPED") return null;
    var plan = day(row.planned_end);
    return plan !== null && plan < scan ? plan : null;
  }

  /* Rows that say the same thing, said once.

     Sixteen planning tasks with the same start, the same status and one of two
     due dates drew sixteen near-identical bars - a wall the eye reads as
     "a lot of work" and takes nothing else from. Collapsed, the same fact is
     three rows and the four genuinely distinct items stop being lost in it.

     Nothing is hidden: the count is in the label and every member's name is in
     the row's tooltip. What is given up is one bar per task, and a bar that is
     pixel-identical to the fifteen above it was never carrying that task's
     information anyway.

     Two rows are only merged when there is nothing to tell them apart on this
     chart - same band, same dates, same status - and never when either is on
     the driving path or has an edge, because an arrow has to land on a row and
     a merged row is not one. */
  var COLLAPSE_AT = 3;

  function mergeKey(row) {
    if (row.on_driving_path) return null;
    return [
      row.start || "-", row.planned_end || "-", row.projected_end || "-",
      row.baseline_end || "-", row.status || "-",
      //: Predecessors are part of what makes two rows the same row here, not a
      //: reason to refuse merging. Sixteen tasks that share dates *and* share
      //: the same single predecessor are sixteen identical statements; keeping
      //: them apart drew sixteen identical bars to preserve arrows that all
      //: pointed at the same place.
      //:
      //: The cost, stated: an arrow cannot land on a merged row, because a
      //: merged row has no `entity_id` to anchor to. That is the right trade -
      //: a chain into a summary is not a chain anybody can trace, and the
      //: members are named in the tooltip.
      (row.depends_on || []).slice().sort().join(",")
    ].join("|");
  }

  /* The name a collapsed row goes by: the tasks in it.

     The first attempt used the bracketed tag every member shared - "7 x
     [Planning Task]" - which is exactly backwards. A tag is only worth printing
     when it tells rows apart, and a tag shared by all sixteen tells you nothing
     except that you are looking at a project whose summaries all begin the same
     way. It read as a category nobody can act on.

     So the shared prefix is stripped and what is left - the part that actually
     differs - is listed until the gutter runs out. A PM scanning for "Develop
     CM Plan" can find it; "7 x [Planning Task]" made that impossible without
     hovering. The remainder is counted, never dropped silently. */
  var LABEL_BUDGET = 44;

  function sharedPrefix(rows) {
    var first = rows[0].title || "";
    var end = first.length;
    for (var i = 1; i < rows.length; i++) {
      var other = rows[i].title || "", j = 0;
      while (j < end && j < other.length && first[j] === other[j]) j++;
      end = j;
    }
    //: Only cut at a word boundary, so "Define OSS"/"Define ISMS" does not
    //: become "OSS"/"ISMS Keyword" split mid-word.
    var cut = first.slice(0, end);
    var space = cut.lastIndexOf(" ");
    return space > 0 ? first.slice(0, space + 1) : "";
  }

  function mergedTitle(rows) {
    var prefix = sharedPrefix(rows);
    var names = rows.map(function (r) {
      return (r.title || r.label || "").slice(prefix.length).trim() || r.label;
    });
    var shown = [], used = 0;
    for (var i = 0; i < names.length; i++) {
      if (used + names[i].length + 2 > LABEL_BUDGET) break;
      shown.push(names[i]);
      used += names[i].length + 2;
    }
    if (!shown.length) return rows.length + " tasks, same dates";
    var rest = names.length - shown.length;
    return shown.join(", ") + (rest ? " +" + rest + " more" : "");
  }

  function collapse(rows) {
    var order = [], byKey = {};
    rows.forEach(function (row) {
      var key = mergeKey(row);
      if (key === null) { order.push({ solo: row }); return; }
      if (!byKey[key]) { byKey[key] = []; order.push({ key: key }); }
      byKey[key].push(row);
    });
    var out = [];
    order.forEach(function (entry) {
      if (entry.solo) { out.push(entry.solo); return; }
      var members = byKey[entry.key];
      if (!members || !members.length) return;
      byKey[entry.key] = null;
      if (members.length < COLLAPSE_AT) { out.push.apply(out, members); return; }
      var first = members[0];
      out.push({
        //: No `entity_id`: a merged row is not a thing an arrow can land on,
        //: and `mergeKey` has already refused to merge anything with an edge.
        entity_id: null,
        label: members.length + " x",
        title: mergedTitle(members),
        status: first.status,
        assignee: null,
        start: first.start,
        baseline_end: first.baseline_end,
        planned_end: first.planned_end,
        projected_end: first.projected_end,
        propagated_days: first.propagated_days,
        recorded_slip_days: null,
        is_inconsistent: false,
        progress: null,
        milestone_id: first.milestone_id,
        milestone_name: first.milestone_name,
        depends_on: [],
        on_driving_path: false,
        merged: members
      });
    });
    return out;
  }

  function grouped(bundle) {
    var order = [], byName = {};
    bundle.rows.filter(datable).forEach(function (row) {
      var name = row.milestone_name || "Not under a milestone";
      if (!byName[name]) { byName[name] = []; order.push(name); }
      byName[name].push(row);
    });
    Object.keys(byName).forEach(function (name) {
      byName[name] = collapse(byName[name]);
    });
    order.sort(function (a, b) {
      if (a === "Not under a milestone") return 1;
      if (b === "Not under a milestone") return -1;
      return 0;
    });
    return order.map(function (name) {
      return {
        name: name,
        rows: byName[name],
        milestone: bundle.milestones.filter(function (m) {
          return m.name === name;
        })[0],
      };
    });
  }

  function tooltip(row) {
    var lines = [
      row.label + (row.title && row.title !== row.label ? "  " + row.title : ""),
    ];
    lines.push("plan      " + dash(row.start) + "  to  " + dash(row.planned_end));
    lines.push("baseline  " + dash(row.baseline_end));
    if (row.propagated_days > 0) {
      lines.push("projected " + row.projected_end);
      lines.push(row.propagated_days + " day(s) the sheet does not show");
    } else {
      lines.push("consistent with its dependencies");
    }
    if (row.recorded_slip_days) {
      lines.push(row.recorded_slip_days + " day(s) already recorded vs baseline");
    }
    /* Every task a collapsed row stands for, by name. This is what makes the
       collapse a rendering rather than a summary - the rows are still here, and
       the count in the label is checkable against them. */
    if (row.merged) {
      lines.push("");
      lines.push("these " + row.merged.length + " tasks share those dates:");
      row.merged.forEach(function (m) {
        lines.push("  " + m.label + "  " + (m.title || ""));
      });
    }
    return lines.join("\n");
  }

  /* The left column: full task titles, never truncated, never scrolled away.

     `undated` is the count of rows left off the plot; it gets its own label
     line so the two halves of the frame stay in step - every band the plot
     draws has to have exactly one label here at exactly the same height, and a
     row added to one and not the other shifts every label below it. */
  function labelColumn(groups, undated, scan) {
    var col = el("div", "g-left");
    groups.forEach(function (group) {
      var head = el("div", "g-group");
      head.appendChild(el("span", "g-group-name", group.name));
      if (group.milestone && group.milestone.at_risk) {
        head.appendChild(el("span", "g-risk", "at risk"));
      }
      col.appendChild(head);

      group.rows.forEach(function (row) {
        /* Past due, marked in the labels as well as on the plot. The red tail
           is only visible if you are looking at that row's bar; a PM scanning
           the list for what to chase should not have to read the chart to find
           it. */
        var late = scan && row.planned_end && row.planned_end < scan &&
          row.status !== "DONE" && row.status !== "DROPPED";
        var line = el("div", "g-row" + (row.on_driving_path ? " driving" : "") +
          (late ? " late-row" : ""));
        /* A row with no Task ID is labelled by its own title (see
           `assembler.entity_label`), so printing both puts the same words
           twice - once in the code font meant for an id, once truncated. Show
           the title alone in that case: it is the only name there is. */
        var titled = row.title && row.title !== row.label;
        line.appendChild(el("span", titled ? "g-id" : "g-title", row.label));
        if (titled) {
          line.appendChild(el("span", "g-title", row.title));
        }
        if (row.propagated_days > 0) {
          line.appendChild(el("span", "g-slip", "+" + row.propagated_days + "d"));
        } else if (late) {
          //: How late, in days, where the chain cannot say. The one lateness
          //: number available without a graph, in the same slot the propagated
          //: figure uses so the column reads consistently either way.
          var days = Math.round(
            (Date.parse(scan) - Date.parse(row.planned_end)) / 86400000
          );
          line.appendChild(el("span", "g-late", days + "d late"));
        }
        line.title = tooltip(row);
        col.appendChild(line);
      });
    });
    if (undated) {
      var tail = el("div", "g-row g-undated-row");
      tail.appendChild(el("span", "g-title", undated + " tasks, no dates"));
      tail.title =
        undated + " task(s) carry no start and no due date, so they have no " +
        "position on this axis. They are counted in every figure on the " +
        "Insight page.";
      col.appendChild(tail);
    }
    return col;
  }

  /* The plot. Width follows the project's own length, so a long project scrolls
     rather than compressing every bar into illegibility. */
  function plot(bundle, groups) {
    var lo = day(bundle.window_start), hi = day(bundle.window_end);
    if (lo === null || hi === null) return null;
    /* Every row undated. An empty axis with month labels and nothing under it
       reads as a chart that failed to load; `render` says it in words. */
    if (!groups.length) return null;

    var span = Math.max(hi - lo, 1);
    var W = Math.max(Math.round(span * PX_PER_DAY), MIN_PLOT);
    var TOP = 26;
    var undatedRows = bundle.rows.length - bundle.rows.filter(datable).length ? 1 : 0;
    var H = TOP + groups.reduce(function (acc, g) {
      return acc + GROUP + g.rows.length * ROW;
    }, 0) + undatedRows * (ROW + 6) + 8;
    var x = function (d) { return ((d - lo) / span) * W; };

    var root = svg("svg", {
      viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": bundle.rows.length + " tasks; " +
        bundle.rows.filter(function (r) { return r.is_inconsistent; }).length +
        " have a planned date the dependency chain cannot support"
    });

    // Month gridlines: solid and recessive, never dashed.
    var cursor = new Date(lo * 86400000);
    cursor.setUTCDate(1);
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
    while (cursor.getTime() / 86400000 <= hi) {
      var gx = x(cursor.getTime() / 86400000);
      root.appendChild(svg("line", { x1: gx, x2: gx, y1: TOP - 14, y2: H - 6, class: "grid" }));
      root.appendChild(svgText(
        { x: gx + 4, y: TOP - 17, class: "dim" },
        cursor.toLocaleString("en", { month: "short", timeZone: "UTC" })
      ));
      cursor.setUTCMonth(cursor.getUTCMonth() + 1);
    }

    // The scan the view reflects. Labelled, because an unexplained vertical
    // line on a Gantt is read as "today" whether or not it is.
    var asOf = day(bundle.as_of);
    if (asOf !== null && asOf >= lo && asOf <= hi) {
      root.appendChild(svg({ x: 0 } && "line", {
        x1: x(asOf), x2: x(asOf), y1: TOP - 14, y2: H - 6, class: "asof"
      }));
      root.appendChild(svgText(
        { x: x(asOf) + 4, y: H - 8, class: "dim" }, "observed " + bundle.as_of
      ));
    }

    var y = TOP, anchor = {};

    groups.forEach(function (group) {
      var ms = group.milestone;
      if (ms && ms.planned_date) {
        var mx = x(day(ms.planned_date)), my = y + 10;
        var diamond = svg("path", {
          d: "M" + mx + " " + (my - 6) + "L" + (mx + 6) + " " + my +
             "L" + mx + " " + (my + 6) + "L" + (mx - 6) + " " + my + "Z",
          fill: ms.at_risk ? "var(--viz-over)" : "var(--ink-2)"
        });
        var mt = svg("title");
        mt.textContent = ms.name + " planned " + ms.planned_date +
          (ms.slipped_days ? "\n" + ms.slipped_days + " day(s) after baseline" : "") +
          (ms.at_risk ? "\nat risk - work beneath it cannot finish on time" : "");
        diamond.appendChild(mt);
        root.appendChild(diamond);
      }
      y += GROUP;

      group.rows.forEach(function (row) {
        var g = svg("g", { class: "prow" });
        g.appendChild(svg("rect", {
          class: "hit", x: 0, y: y, width: W, height: ROW, fill: "transparent"
        }));

        var start = day(row.start), plan = day(row.planned_end);
        var proj = day(row.projected_end), base = day(row.baseline_end);
        var tip = svg("title");
        tip.textContent = tooltip(row);

        if (start === null && plan !== null) {
          // A source that gives an end date and no start - Jira due dates do
          // this. A point marker is honest; a bar from an assumed start would
          // be a date we invented.
          var px = x(plan);
          var point = svg("circle", {
            class: "point", cx: px, cy: y + 10, r: 5,
            fill: "var(--viz-plan)"
          });
          point.appendChild(tip);
          var lateFrom = lateAsAt(row, asOf);
          if (lateFrom !== null) {
            /* Behind the marker, so the dot stays the thing the eye lands on -
               the tail is how far, the dot is the date. */
            g.appendChild(svg("rect", {
              class: "late", x: x(lateFrom), y: y + 6,
              width: Math.max(x(asOf) - x(lateFrom), 2), height: BAR_H - 4, rx: 2
            }));
          }
          g.appendChild(point);
          /* The date, and not the explanation. "- no start date" was true and
             repeated it on every such row; with a whole export of them it became
             the loudest text on the chart. The legend carries the meaning once,
             which is where a thing that is true of every marker belongs. */
          g.appendChild(svgText(
            { x: px + 10, y: y + 14, class: "dim" }, "due " + row.planned_end
          ));
        } else if (start !== null && plan !== null) {
          // Baseline: a recessive strip beneath the bar, not a competing hue.
          if (base !== null && base > start) {
            g.appendChild(svg("rect", {
              x: x(start), y: y + 18, width: Math.max(x(base) - x(start), 2),
              height: 3, rx: 1.5, fill: "var(--viz-base)", opacity: ".55"
            }));
          }

          var lateFrom = lateAsAt(row, asOf);
          if (lateFrom !== null) {
            g.appendChild(svg("rect", {
              class: "late", x: x(lateFrom), y: y + 4,
              width: Math.max(x(asOf) - x(lateFrom), 2), height: BAR_H, rx: 3
            }));
          }

          var overrun = proj !== null && proj > plan;
          var bar = svg("rect", {
            class: "mark", x: x(start), y: y + 4,
            width: Math.max(x(plan) - x(start), 3), height: BAR_H,
            rx: overrun ? 0 : 4, fill: "var(--viz-plan)"
          });
          /* Not started, drawn hollow. The dates are a plan either way, so it
             is the same bar - but a task nobody has picked up is a different
             thing from one underway, and on a board where "In Progress" was a
             bulk transition it is often the only honest distinction left. */
          if (row.status === "TODO") {
            bar.setAttribute("fill", "none");
            bar.setAttribute("stroke", "var(--viz-plan)");
            bar.setAttribute("stroke-width", "1.5");
            bar.setAttribute("stroke-dasharray", "4 3");
          }
          bar.appendChild(tip);
          g.appendChild(bar);

          // Progress, as a darker inset - a second encoding of a number that is
          // already in the tooltip, so it can be ignored safely.
          if (row.progress > 0) {
            g.appendChild(svg("rect", {
              x: x(start), y: y + 4,
              width: Math.max((x(plan) - x(start)) * (row.progress / 100), 2),
              height: BAR_H, rx: 3, fill: "var(--ink)", opacity: ".22"
            }));
          }

          if (overrun) {
            // 2px surface gap, so the two fills read as two separate facts.
            g.appendChild(svg("rect", {
              class: "mark", x: x(plan) + 2, y: y + 4,
              width: Math.max(x(proj) - x(plan) - 2, 3), height: BAR_H,
              rx: 4, fill: "var(--viz-over)"
            }));

            // The longest bar ends at the window edge, so a label placed after
            // it is clipped - which is how the worst slip on the chart became
            // the one figure you could not read. Flip it inside the bar instead.
            var text = "+" + row.propagated_days + "d";
            var needs = text.length * 6 + 8;
            var outside = x(proj) + needs < W;
            g.appendChild(svgText({
              x: outside ? x(proj) + 7 : x(proj) - 7,
              y: y + 14,
              class: "val",
              "text-anchor": outside ? "start" : "end",
              fill: outside ? null : "#fff"
            }, text));
          }
          anchor[row.entity_id] = { x2: x(plan), x1: x(start), y: y + 11 };
        }

        root.appendChild(g);
        y += ROW;
      });
    });

    /* The undated, as volume rather than as a sentence.

       These rows have no position in time and never will - that is why they are
       off the plot above. But "170 of 190" as a line of text under the legend
       reads as a caveat, and it is not a caveat: on this export it is most of
       the project. A band the full width of the window, hatched so it cannot be
       mistaken for a bar that means something, says the same number in the
       units the rest of the chart is in.

       Full width is not a claim that the work spans the window. Nothing else on
       the chart is hatched, and the label says "no dates" - a bar that obviously
       refuses to be read as a date range is the honest way to draw a quantity
       on a time axis. */
    var undatedCount = bundle.rows.length - bundle.rows.filter(datable).length;
    if (undatedCount) {
      y += 6;
      var band = svg("g", { class: "prow" });
      band.appendChild(svg("rect", {
        class: "undated", x: 0, y: y + 4, width: W, height: BAR_H, rx: 3
      }));
      var ut = svg("title");
      ut.textContent =
        undatedCount + " task(s) carry no start and no due date.\n" +
        "They have no position on this axis. Counted in every figure on the " +
        "Insight page.";
      band.appendChild(ut);
      root.appendChild(band);
      y += ROW;
    }

    // Dependency arrows last, above the bars they connect.
    bundle.rows.forEach(function (row) {
      var to = anchor[row.entity_id];
      if (!to) return;
      (row.depends_on || []).forEach(function (predId) {
        var from = anchor[predId];
        if (!from) return;
        var driving = row.on_driving_path &&
          bundle.driving_path.indexOf(predId) !== -1;
        var cls = "dep" + (driving ? " driving" : "");
        var mid = Math.max(from.x2 + 7, to.x1 - 7);
        root.appendChild(svg("path", {
          class: cls,
          d: "M" + from.x2 + " " + from.y + "H" + mid + "V" + to.y + "H" + to.x1
        }));
        root.appendChild(svg("path", {
          class: cls,
          d: "M" + (to.x1 - 5) + " " + (to.y - 3.5) + "L" + to.x1 + " " + to.y +
             "L" + (to.x1 - 5) + " " + (to.y + 3.5)
        }));
      });
    });

    return root;
  }

  function key(bundle) {
    var wrap = el("div", "g-key");
    var items = [
      ["base", "baseline - what was committed"],
      ["plan", "plan - what the sheet says today"],
      ["over", "beyond the plan - what the chain implies"],
      ["ms", "milestone"]
    ];
    if (bundle.rows.some(function (r) { return !r.start && r.planned_end; })) {
      items.push(["point", "due date only - no start in the source"]);
    }
    items.push(["late", "past due - from the date to the scan"]);
    items.forEach(function (pair) {
      var item = el("span");
      item.appendChild(el("i", pair[0]));
      item.appendChild(document.createTextNode(pair[1]));
      wrap.appendChild(item);
    });

    /* Said plainly, because a chart that quietly drops rows is worse than one
       that draws them badly. The number is the whole point: 170 of 190 is a
       statement about the source, not about the chart. */
    var undated = bundle.rows.length - bundle.rows.filter(datable).length;
    if (undated) {
      var note = el("span", "g-omitted");
      note.appendChild(document.createTextNode(
        undated + " of " + bundle.rows.length +
        " task(s) carry no start and no due date, so they are not on this " +
        "chart. They are counted everywhere else."
      ));
      wrap.appendChild(note);
    }
    return wrap;
  }

  /* Build the whole chart. `opts.key` draws the legend; the embedded copy on the
     Calculation tab turns it off because that page explains the bars in words
     directly beneath them. */
  function render(bundle, opts) {
    opts = opts || {};
    var groups = grouped(bundle);
    var body = plot(bundle, groups);

    var wrap = el("div", "gantt viz");
    if (opts.key !== false) wrap.appendChild(key(bundle));

    var frame = el("div", "g-frame");
    frame.appendChild(
      labelColumn(
        groups,
        bundle.rows.length - bundle.rows.filter(datable).length,
        bundle.as_of || ""
      )
    );

    var scroller = el("div", "g-plot");
    if (body) scroller.appendChild(body);
    else scroller.appendChild(el("p", "g-empty",
      bundle.rows.length
        ? "None of the " + bundle.rows.length + " task(s) here carries a start " +
          "or a due date, so there is nothing to place on a time axis."
        : "No dated tasks to draw."));
    frame.appendChild(scroller);

    wrap.appendChild(frame);
    return wrap;
  }

  global.PulseGantt = { render: render, ROW: ROW, GROUP: GROUP };
})(window);
