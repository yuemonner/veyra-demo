"use client";
/* Language of the case view. A case written in Chinese (its title says so) is shown with Chinese labels; technical
   terms stay in English. Everything else stays in English. */
import { createContext, useContext } from "react";

export type Lang = "en" | "zh";
export const LangContext = createContext<Lang>("en");
export const useZh = () => useContext(LangContext) === "zh";
export const langOf = (title?: string | null): Lang => (/[一-鿿]/.test(title || "") ? "zh" : "en");
