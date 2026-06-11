import { redirect } from "next/navigation";

/**
 * Site root — Phase C redesign.
 *
 * The landing is now the Roadmap lifeline (`/roadmap`), the redesigned
 * showpiece that narrates the agent's lifecycle. The former live
 * telemetry dashboard (the old root content) lives unchanged at `/live`.
 *
 * We redirect rather than render so there is a single canonical home and
 * the roadmap is reachable at `/`.
 */
export default function RootPage(): never {
  redirect("/roadmap");
}
