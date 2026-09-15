import * as React from "react"

export type ToastVariant = "default" | "destructive" | "success"

export interface Toast {
  id: number
  title: string
  description?: string
  variant?: ToastVariant
}

interface ToastContextValue {
  toast: (options: Omit<Toast, "id">) => void
}

const ToastContext = React.createContext<ToastContextValue | null>(null)

let toastId = 0

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = React.useState<Toast[]>([])

  const toast = React.useCallback((options: Omit<Toast, "id">) => {
    const id = ++toastId
    setToasts((prev) => [...prev, { ...options, id }])
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 5000)
  }, [])

  return (
    React.createElement(ToastContext.Provider, { value: { toast } },
      React.createElement(React.Fragment, null,
        children,
        React.createElement("div", { className: "fixed bottom-4 right-4 z-50 flex flex-col gap-2" },
          toasts.map((t) => {
            let className = "rounded-md border px-4 py-3 shadow-lg"
            if (t.variant === "destructive") {
              className = className + " border-destructive bg-destructive/10 text-destructive"
            } else if (t.variant === "success") {
              className = className + " border-success bg-success/10 text-success"
            } else {
              className = className + " border-border bg-background text-foreground"
            }
            return React.createElement("div", { key: t.id, className: className },
              React.createElement("div", { className: "font-medium" }, t.title),
              t.description ? React.createElement("div", { className: "text-sm opacity-80" }, t.description) : null
            )
          })
        )
      )
    )
  )
}

export function useToast() {
  const context = React.useContext(ToastContext)
  if (!context) {
    throw new Error("useToast must be used within a ToastProvider")
  }
  return context
}