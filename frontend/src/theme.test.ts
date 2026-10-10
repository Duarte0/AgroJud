/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const stylesheet = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");
const rootTokenBlock = stylesheet.match(/:root\s*\{([^}]+)\}/);

if (!rootTokenBlock?.[1]) throw new Error("Não foi possível ler os tokens do tema em index.css.");
const rootTokens = rootTokenBlock[1];

function token(name: string): string {
  const declaration = rootTokens.match(new RegExp(`--${name}:\\s*([^;]+);`))?.[1]?.trim();
  if (!declaration) throw new Error(`Token de tema ausente: --${name}`);
  const reference = declaration.match(/^var\(--([^)]+)\)$/)?.[1];
  return reference ? token(reference) : declaration;
}

function luminance(color: string): number {
  const channels = color.match(/^#([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i);
  if (!channels) throw new Error(`Cor não suportada no teste: ${color}`);
  const linear = [channels[1]!, channels[2]!, channels[3]!].map((channel) => {
    const value = Number.parseInt(channel, 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrast(foreground: string, background: string): number {
  const ordered = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  const lighter = ordered[0]!;
  const darker = ordered[1]!;
  return (lighter + 0.05) / (darker + 0.05);
}

describe("identidade cromática", () => {
  it("mantém a paleta de marca centralizada nos tokens", () => {
    expect({
      primary: token("primary"),
      primaryDark: token("primary-dark"),
      primarySoft: token("primary-soft"),
      accent: token("accent"),
      background: token("background"),
      surface: token("surface"),
      border: token("border"),
      textPrimary: token("text-primary"),
      textSecondary: token("text-secondary"),
    }).toEqual({
      primary: "#1F5D42",
      primaryDark: "#163C2D",
      primarySoft: "#EAF3EE",
      accent: "#B68A3A",
      background: "#F7F8F5",
      surface: "#FFFFFF",
      border: "#DDE2DD",
      textPrimary: "#18211B",
      textSecondary: "#667069",
    });
  });

  it("mantém contraste para textos, estados e limites interativos", () => {
    const textPairs: Array<[string, string]> = [
      ["text-primary", "background"],
      ["text-secondary", "background"],
      ["text-secondary", "surface"],
      ["primary-foreground", "primary"],
      ["primary-dark", "primary-soft"],
      ["accent-foreground", "accent"],
      ["warning-foreground", "warning"],
      ["info-foreground", "info"],
      ["success-foreground", "success"],
      ["destructive-foreground", "destructive"],
    ];
    for (const [foreground, background] of textPairs) {
      expect(
        contrast(token(foreground), token(background)),
        `${foreground} sobre ${background}`,
      ).toBeGreaterThanOrEqual(4.5);
    }

    expect(contrast(token("input"), token("surface")), "borda de controle sobre superfície")
      .toBeGreaterThanOrEqual(3);
    expect(contrast(token("accent"), token("surface")), "marcador dourado sobre superfície")
      .toBeGreaterThanOrEqual(3);
  });
});
