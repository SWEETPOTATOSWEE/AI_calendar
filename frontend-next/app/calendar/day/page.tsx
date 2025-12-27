import { redirect } from "next/navigation";

export default function CalendarDayPage() {
  redirect("/calendar?view=day");
}
