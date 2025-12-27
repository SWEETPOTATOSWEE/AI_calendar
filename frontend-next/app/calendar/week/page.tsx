import { redirect } from "next/navigation";

export default function CalendarWeekPage() {
  redirect("/calendar?view=week");
}
