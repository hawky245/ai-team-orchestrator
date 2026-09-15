import * as React from "react"
import { cn } from "@/lib/utils"

export interface ScrollAreaProps extends React.HTMLAttributes<HTMLDivElement> {
  orientation?: "vertical" | "horizontal" | "both"
}

const ScrollArea = React.forwardRef<HTMLDivElement, ScrollAreaProps>(
  ({ className, orientation = "vertical", children, ...props }, ref) => {
    const vertical = orientation === "vertical" || orientation === "both"
    const horizontal = orientation === "horizontal" || orientation === "both"
    return (
      <div
        ref={ref}
        className={cn(
          "relative overflow-hidden",
          vertical && "overflow-y-auto",
          horizontal && "overflow-x-auto",
          className
        )}
        {...props}
      >
        {children}
      </div>
    )
  }
)
ScrollArea.displayName = "ScrollArea"

export { ScrollArea }