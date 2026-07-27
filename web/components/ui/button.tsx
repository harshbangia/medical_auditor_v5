import { cn } from "@/lib/utils";
import { type ButtonHTMLAttributes, forwardRef } from "react";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", ...props }, ref) => {
    const variants = {
      primary:
        "bg-gradient-to-r from-cyan-500 to-blue-600 text-white shadow-lg shadow-cyan-500/25 hover:brightness-110",
      secondary:
        "bg-slate-800 text-slate-100 border border-slate-700 hover:bg-slate-700",
      ghost: "text-slate-300 hover:bg-slate-800/80 hover:text-white",
      danger: "bg-rose-600/90 text-white hover:bg-rose-600",
    };
    const sizes = {
      sm: "h-9 px-3 text-sm rounded-lg",
      md: "h-11 px-5 text-sm rounded-xl",
      lg: "h-12 px-6 text-base rounded-xl",
    };
    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex items-center justify-center gap-2 font-semibold transition disabled:opacity-50 disabled:pointer-events-none",
          variants[variant],
          sizes[size],
          className,
        )}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";
