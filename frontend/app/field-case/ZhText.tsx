"use client";
/* Chinese labels for a case written in Chinese. The interface strings are English in the components; while a Chinese
   case is open, this swaps each interface string for its Chinese wording as it is rendered. Data never changes: log
   lines, quotes, code and anything marked data-no-zh are left exactly as they are. Technical terms stay in English. */
import { useEffect } from "react";
import { zh } from "../../lib/zh-labels";

const SKIP = "pre, code, textarea, input, select, svg, [data-no-zh]";

const original = new Map<Node, { from: string; to: string }>();

function translate(node: Node) {
  if (node.nodeType === Node.TEXT_NODE) {
    const el = node.parentElement;
    if (!el || el.closest(SKIP)) return;
    const out = zh(node.nodeValue || "");
    if (out !== null && out !== node.nodeValue) { original.set(node, { from: node.nodeValue || "", to: out }); node.nodeValue = out; }
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE || (node as Element).matches(SKIP)) return;
  const w = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  const all: Node[] = [];
  while (w.nextNode()) all.push(w.currentNode);
  all.forEach(translate);
}

/** Mounted while a Chinese case is open. */
export function ZhText() {
  useEffect(() => {
    translate(document.body);
    const obs = new MutationObserver((list) => {
      for (const m of list) {
        if (m.type === "characterData") translate(m.target);
        else m.addedNodes.forEach(translate);
      }
    });
    obs.observe(document.body, { subtree: true, childList: true, characterData: true });
    document.documentElement.lang = "zh-CN";
    return () => {
      obs.disconnect();
      document.documentElement.lang = "en";
      // Put back every label this changed and the page still shows, so an English case opened next reads in English.
      original.forEach((v, n) => { if (n.isConnected && n.nodeValue === v.to) n.nodeValue = v.from; });
      original.clear();
    };
  }, []);
  return null;
}
