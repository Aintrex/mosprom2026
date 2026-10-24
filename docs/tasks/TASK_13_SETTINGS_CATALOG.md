# Каталог параметров для ТЗ 13

24.10.2026. Кандидаты подтверждены по `resources/definitions/fdmprinter.def.json` выданного STE Slicer (автор определений Stereotech/исходный форк Cura). Это перечень ключей и типов для реализации, а не подтверждение доступности/записи всех настроек в запущенном STE.

Цепочка основного определения: `hte530v5.2.4 -> steprinter530_v5.2.4 -> steprinter_v5 -> fdmprinter`. Противоречащих type overrides для этих ключей в цепочке не обнаружено. Runtime должен независимо подтвердить тип, область записи, enabled и пределы; это не статические значения из файла.

**120 ключей, включая прежние 19.** `printing_mode` не включён: для него остаётся отдельный set_print_mode с подтверждением backend. Машинные размеры, стартовый/конечный G-code и команды движка не открываются.

Названия ниже взяты из локального определения, только как справка. Полный внешний JSON и абсолютный путь машины не являются runtime-зависимостями плагина.

## Качество и ширина линий

`category=resolution`; ключей: 12.

| Ключ | Тип | Название в определении |
| --- | --- | --- |
| `layer_height` | `float` | Layer Height |
| `layer_height_0` | `float` | Initial Layer Height |
| `cylindrical_layer_height` | `float` | 5D Surface Layer Height |
| `cylindrical_layer_height_0` | `float` | Initial 5D Surface Layer Height |
| `classic_layer_height` | `float` | Classic Layer Height |
| `line_width` | `float` | Line Width |
| `wall_line_width` | `float` | Wall Line Width |
| `wall_line_width_0` | `float` | Outer Wall Line Width |
| `wall_line_width_x` | `float` | Inner Wall(s) Line Width |
| `skin_line_width` | `float` | Top/Bottom Line Width |
| `infill_line_width` | `float` | Infill Line Width |
| `initial_layer_line_width_factor` | `float` | Initial Layer Line Width |

## Стенки и верхние и нижние поверхности

`category=shell`; ключей: 11.

| Ключ | Тип | Название в определении |
| --- | --- | --- |
| `wall_thickness` | `float` | Wall Thickness |
| `wall_line_count` | `int` | Wall Line Count |
| `top_bottom_thickness` | `float` | Top/Bottom Thickness |
| `top_thickness` | `float` | Top Thickness |
| `top_layers` | `int` | Top Layers |
| `bottom_thickness` | `float` | Bottom Thickness |
| `bottom_layers` | `int` | Bottom Layers |
| `xy_offset` | `float` | Horizontal Expansion |
| `xy_offset_layer_0` | `float` | Initial Layer Horizontal Expansion |
| `z_seam_type` | `enum` | Z Seam Alignment |
| `z_seam_corner` | `enum` | Seam Corner Preference |

## Заполнение

`category=infill`; ключей: 11.

| Ключ | Тип | Название в определении |
| --- | --- | --- |
| `infill_sparse_density` | `float` | Infill Density |
| `infill_pattern` | `enum` | Infill Pattern |
| `infill_line_distance` | `float` | Infill Line Distance |
| `infill_overlap` | `float` | Infill Overlap Percentage |
| `infill_overlap_mm` | `float` | Infill Overlap |
| `infill_before_walls` | `bool` | Infill Before Walls |
| `infill_wipe_dist` | `float` | Infill Wipe Distance |
| `infill_multiplier` | `int` | Infill Line Multiplier |
| `infill_sparse_thickness` | `float` | Infill Layer Thickness |
| `gradual_infill_steps` | `int` | Gradual Infill Steps |
| `gradual_infill_step_height` | `float` | Gradual Infill Step Height |

## Температуры подача и ретракт

`category=material`; ключей: 20.

| Ключ | Тип | Название в определении |
| --- | --- | --- |
| `material_print_temperature` | `float` | Printing Temperature |
| `material_print_temperature_layer_0` | `float` | Printing Temperature Initial Layer |
| `material_initial_print_temperature` | `float` | Initial Printing Temperature |
| `material_final_print_temperature` | `float` | Final Printing Temperature |
| `material_bed_temperature` | `float` | Build Plate Temperature |
| `material_bed_temperature_layer_0` | `float` | Build Plate Temperature Initial Layer |
| `material_flow` | `float` | Flow |
| `wall_material_flow` | `float` | Wall Flow |
| `infill_material_flow` | `float` | Infill Flow |
| `skin_material_flow` | `float` | Top/Bottom Flow |
| `material_flow_layer_0` | `float` | Initial Layer Flow |
| `retraction_enable` | `bool` | Enable Retraction |
| `retraction_amount` | `float` | Retraction Distance |
| `retraction_speed` | `float` | Retraction Speed |
| `retraction_retract_speed` | `float` | Retraction Retract Speed |
| `retraction_prime_speed` | `float` | Retraction Prime Speed |
| `retraction_extra_prime_amount` | `float` | Retraction Extra Prime Amount |
| `retraction_min_travel` | `float` | Retraction Minimum Travel |
| `retraction_count_max` | `int` | Maximum Retraction Count |
| `retraction_extrusion_window` | `float` | Minimum Extrusion Distance Window |

## Скорости

`category=speed`; ключей: 14.

| Ключ | Тип | Название в определении |
| --- | --- | --- |
| `speed_print` | `float` | Print Speed |
| `speed_infill` | `float` | Infill Speed |
| `speed_wall` | `float` | Wall Speed |
| `speed_wall_0` | `float` | Outer Wall Speed |
| `speed_wall_x` | `float` | Inner Wall Speed |
| `speed_topbottom` | `float` | Top/Bottom Speed |
| `speed_support` | `float` | Support Speed |
| `speed_support_infill` | `float` | Support Infill Speed |
| `speed_support_interface` | `float` | Support Interface Speed |
| `speed_travel` | `float` | Travel Speed |
| `speed_layer_0` | `float` | Initial Layer Speed |
| `speed_print_layer_0` | `float` | Initial Layer Print Speed |
| `speed_travel_layer_0` | `float` | Initial Layer Travel Speed |
| `speed_slowdown_layers` | `int` | Number of Slower Layers |

## Перемещения и подъём сопла

`category=travel`; ключей: 6.

| Ключ | Тип | Название в определении |
| --- | --- | --- |
| `retraction_hop_enabled` | `bool` | Z Hop When Retracted |
| `retraction_hop` | `float` | Z Hop Height |
| `retraction_combing` | `enum` | Combing Mode |
| `travel_avoid_other_parts` | `bool` | Avoid Printed Parts When Traveling |
| `travel_avoid_supports` | `bool` | Avoid Supports When Traveling |
| `travel_avoid_distance` | `float` | Travel Avoid Distance |

## Охлаждение

`category=cooling`; ключей: 9.

| Ключ | Тип | Название в определении |
| --- | --- | --- |
| `cool_fan_enabled` | `bool` | Enable Print Cooling |
| `cool_fan_speed` | `float` | Fan Speed |
| `cool_fan_speed_min` | `float` | Regular Fan Speed |
| `cool_fan_speed_max` | `float` | Maximum Fan Speed |
| `cool_min_layer_time` | `float` | Minimum Layer Time |
| `cool_min_speed` | `float` | Minimum Speed |
| `cool_lift_head` | `bool` | Lift Head |
| `cool_fan_full_at_height` | `float` | Regular Fan Speed at Height |
| `cool_fan_full_layer` | `int` | Regular Fan Speed at Layer |

## Поддержки

`category=support`; ключей: 20.

| Ключ | Тип | Название в определении |
| --- | --- | --- |
| `support_enable` | `bool` | Generate Support |
| `support_type` | `enum` | Support Placement |
| `support_angle` | `float` | Support Overhang Angle |
| `support_pattern` | `enum` | Support Pattern |
| `support_infill_rate` | `float` | Support Density |
| `support_line_distance` | `float` | Support Line Distance |
| `support_z_distance` | `float` | Classic Support Z Distance |
| `support_top_distance` | `float` | Classic Support Top Distance |
| `support_bottom_distance` | `float` | Classic Support Bottom Distance |
| `support_xy_distance` | `float` | Classic Support X/Y Distance |
| `support_interface_enable` | `bool` | Enable Support Interface |
| `support_interface_height` | `float` | Support Interface Thickness |
| `support_interface_density` | `float` | Support Interface Density |
| `support_interface_pattern` | `enum` | Support Interface Pattern |
| `support_roof_enable` | `bool` | Enable Support Roof |
| `support_roof_height` | `float` | Support Roof Thickness |
| `support_roof_density` | `float` | Support Roof Density |
| `support_bottom_enable` | `bool` | Enable Support Floor |
| `support_bottom_height` | `float` | Support Floor Thickness |
| `support_bottom_density` | `float` | Support Floor Density |

## Адгезия к столу

`category=platform_adhesion`; ключей: 13.

| Ключ | Тип | Название в определении |
| --- | --- | --- |
| `adhesion_type` | `enum` | Build Plate Adhesion Type |
| `skirt_line_count` | `int` | Skirt Line Count |
| `skirt_gap` | `float` | Skirt Distance |
| `skirt_brim_minimal_length` | `float` | Skirt/Brim Minimum Length |
| `brim_width` | `float` | Brim Width |
| `brim_line_count` | `int` | Brim Line Count |
| `brim_outside_only` | `bool` | Brim Only on Outside |
| `raft_margin` | `float` | Raft Extra Margin |
| `raft_airgap` | `float` | Raft Air Gap |
| `raft_surface_layers` | `int` | Raft Top Layers |
| `raft_surface_thickness` | `float` | Raft Top Layer Thickness |
| `raft_interface_thickness` | `float` | Raft Middle Thickness |
| `raft_base_thickness` | `float` | Raft Base Thickness |

## Параметры основания 5D

`category=printing_mode_category`; ключей: 4.

| Ключ | Тип | Название в определении |
| --- | --- | --- |
| `cylindrical_mode_base_diameter` | `float` | Base Diameter |
| `cylindrical_mode_overlap` | `float` | Cylindrical Parts Overlap |
| `non_printing_base_diameter` | `float` | Non Printing Base Diameter |
| `cylindrical_round_segments` | `float` | Cylindrical Round Segments |

## Особенности

- `cylindrical_round_segments` в этом форке имеет тип **float**, несмотря на смысл количества сегментов. Не подменять тип на int по названию.
- Ретракт в определении относится к категории material, combing и Z-hop — к travel. Это учесть в поиске/описании категорий.
- `support_structure` в просмотренном определении отсутствует: его нельзя добавлять по памяти о современной Cura.
- Разрешены чтение и запись только через существующие инструменты, проверку runtime и QtBridge. Наличие ключа в каталоге не гарантирует, что текущий профиль разрешает его изменение.
