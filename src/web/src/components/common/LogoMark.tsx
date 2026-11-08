import logoUrl from "../../assets/branding/fintrix-logo.svg";

export function LogoMark({ height, width }: { height?: number; width?: number }) {
  return <img src={logoUrl} alt="FINTRIX" style={{ height, width, display: "block" }} />;
}
