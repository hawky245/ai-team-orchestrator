/* Brand-tinted monogram badges for model providers.

These are simplified letter/symbol marks in each brand's signature color —
not official logo files — used in the MODELS panel, task nodes, and the
provider status list. Pick a logo with providerLogo(modelId, tag). */

const PROVIDER_BRANDS: Record<string, { color: string; bg?: string; label: string }> = {
  nvidia:     { color: '#76B900', label: 'N' },
  groq:       { color: '#F55036', label: 'G' },
  openrouter: { color: '#4BDEBE', label: 'R' },
  openai:     { color: '#10A37F', label: 'AI' },
  anthropic:  { color: '#D97757', label: 'A' },
  claude:     { color: '#D97757', label: 'A' },
  google:     { color: '#4285F4', label: 'G' },
  gemini:     { color: '#4285F4', label: 'G' },
  meta:       { color: '#0668E1', label: '∞' },
  llama:      { color: '#0668E1', label: '∞' },
  mistral:    { color: '#FD7FFF', label: 'M' },
  deepseek:   { color: '#5599FF', label: 'D' },
  qwen:       { color: '#615CEF', label: 'Q' },
  microsoft:  { color: '#7FBA00', label: 'MS' },
  xai:        { color: '#E5E7EB', label: 'X' },
  command:    { color: '#FF7218', label: 'C' },
  cohere:     { color: '#FF7218', label: 'C' },
  local:      { color: '#22D3EE', label: 'L' },
};

// Which brand a model belongs to: try the id prefix/name, then the
// provider/key tag shown next to it.
export function detectBrand(modelId: string, tag?: string | null): string {
  const hay = `${modelId} ${tag ?? ''}`.toLowerCase();
  const keys = Object.keys(PROVIDER_BRANDS);
  for (const k of keys) if (hay.includes(k)) return k;
  if (/\bollama\b|localhost|127\.0\.0\.1|vllm/.test(hay)) return 'local';
  return 'other';
}

export function providerLogo(
  modelId: string,
  tag?: string | null,
  size = 28
): { text: string; color: string; size: number } {
  const brand = detectBrand(modelId, tag);
  const b = PROVIDER_BRANDS[brand];
  return { text: b ? b.label : '◆', color: b ? b.color : '#67E8F9', size };
}

export function ProviderLogo({ modelId, tag, size = 28 }: {
  modelId: string;
  tag?: string | null;
  size?: number;
}) {
  const { text, color } = providerLogo(modelId, tag, size);
  const fontSize = text.length > 1 ? size * 0.36 : size * 0.46;
  return (
    <span
      aria-hidden
      className="grid shrink-0 place-items-center rounded-full border"
      style={{
        width: size,
        height: size,
        borderColor: `${color}66`,
        background: `${color}14`,
        color,
        fontSize,
        fontWeight: 700,
        fontFamily: 'var(--font-mono)',
        lineHeight: 1,
      }}
    >
      {text}
    </span>
  );
}
