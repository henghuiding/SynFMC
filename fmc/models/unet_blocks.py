# Adapted from https://github.com/huggingface/diffusers/blob/main/src/diffusers/models/unet_2d_blocks.py

import torch
from torch import nn
from einops import rearrange, repeat
from diffusers.models.resnet import Downsample2D, Upsample2D, ResnetBlock2D
from diffusers.models.transformer_2d import Transformer2DModel

from .motion_module import get_motion_module


def get_down_block(
        down_block_type,
        num_layers,
        in_channels,
        out_channels,
        temb_channels,
        add_downsample,
        resnet_eps,
        resnet_act_fn,
        attn_num_head_channels,
        resnet_groups=None,
        cross_attention_dim=None,
        downsample_padding=None,
        dual_cross_attention=False,
        use_linear_projection=False,
        only_cross_attention=False,
        upcast_attention=False,
        resnet_time_scale_shift="default",
        use_motion_module=None,
        motion_module_type=None,
        motion_module_kwargs=None,
):
    down_block_type = down_block_type[7:] if down_block_type.startswith("UNetRes") else down_block_type
    if down_block_type == "DownBlock3D":
        return DownBlock3D(
            num_layers=num_layers,
            in_channels=in_channels,
            out_channels=out_channels,
            temb_channels=temb_channels,
            add_downsample=add_downsample,
            resnet_eps=resnet_eps,
            resnet_act_fn=resnet_act_fn,
            resnet_groups=resnet_groups,
            downsample_padding=downsample_padding,
            resnet_time_scale_shift=resnet_time_scale_shift,
            use_motion_module=use_motion_module,
            motion_module_type=motion_module_type,
            motion_module_kwargs=motion_module_kwargs,
        )
    elif down_block_type == "CrossAttnDownBlock3D":
        if cross_attention_dim is None:
            raise ValueError("cross_attention_dim must be specified for CrossAttnDownBlock3D")
        return CrossAttnDownBlock3D(
            num_layers=num_layers,
            in_channels=in_channels,
            out_channels=out_channels,
            temb_channels=temb_channels,
            add_downsample=add_downsample,
            resnet_eps=resnet_eps,
            resnet_act_fn=resnet_act_fn,
            resnet_groups=resnet_groups,
            downsample_padding=downsample_padding,
            cross_attention_dim=cross_attention_dim,
            attn_num_head_channels=attn_num_head_channels,
            dual_cross_attention=dual_cross_attention,
            use_linear_projection=use_linear_projection,
            only_cross_attention=only_cross_attention,
            upcast_attention=upcast_attention,
            resnet_time_scale_shift=resnet_time_scale_shift,
            use_motion_module=use_motion_module,
            motion_module_type=motion_module_type,
            motion_module_kwargs=motion_module_kwargs,
        )
    raise ValueError(f"{down_block_type} does not exist.")


def get_up_block(
        up_block_type,
        num_layers,
        in_channels,
        out_channels,
        prev_output_channel,
        temb_channels,
        add_upsample,
        resnet_eps,
        resnet_act_fn,
        attn_num_head_channels,
        resnet_groups=None,
        cross_attention_dim=None,
        dual_cross_attention=False,
        use_linear_projection=False,
        only_cross_attention=False,
        upcast_attention=False,
        resnet_time_scale_shift="default",
        use_motion_module=None,
        motion_module_type=None,
        motion_module_kwargs=None,
):
    up_block_type = up_block_type[7:] if up_block_type.startswith("UNetRes") else up_block_type
    if up_block_type == "UpBlock3D":
        return UpBlock3D(
            num_layers=num_layers,
            in_channels=in_channels,
            out_channels=out_channels,
            prev_output_channel=prev_output_channel,
            temb_channels=temb_channels,
            add_upsample=add_upsample,
            resnet_eps=resnet_eps,
            resnet_act_fn=resnet_act_fn,
            resnet_groups=resnet_groups,
            resnet_time_scale_shift=resnet_time_scale_shift,
            use_motion_module=use_motion_module,
            motion_module_type=motion_module_type,
            motion_module_kwargs=motion_module_kwargs,
        )
    elif up_block_type == "CrossAttnUpBlock3D":
        if cross_attention_dim is None:
            raise ValueError("cross_attention_dim must be specified for CrossAttnUpBlock3D")
        return CrossAttnUpBlock3D(
            num_layers=num_layers,
            in_channels=in_channels,
            out_channels=out_channels,
            prev_output_channel=prev_output_channel,
            temb_channels=temb_channels,
            add_upsample=add_upsample,
            resnet_eps=resnet_eps,
            resnet_act_fn=resnet_act_fn,
            resnet_groups=resnet_groups,
            cross_attention_dim=cross_attention_dim,
            attn_num_head_channels=attn_num_head_channels,
            dual_cross_attention=dual_cross_attention,
            use_linear_projection=use_linear_projection,
            only_cross_attention=only_cross_attention,
            upcast_attention=upcast_attention,
            resnet_time_scale_shift=resnet_time_scale_shift,
            use_motion_module=use_motion_module,
            motion_module_type=motion_module_type,
            motion_module_kwargs=motion_module_kwargs,
        )
    raise ValueError(f"{up_block_type} does not exist.")


class UNetMidBlock3DCrossAttn(nn.Module):
    def __init__(
            self,
            in_channels: int,
            temb_channels: int,
            dropout: float = 0.0,
            num_layers: int = 1,
            resnet_eps: float = 1e-6,
            resnet_time_scale_shift: str = "default",
            resnet_act_fn: str = "swish",
            resnet_groups: int = 32,
            resnet_pre_norm: bool = True,
            attn_num_head_channels=1,
            output_scale_factor=1.0,
            cross_attention_dim=1280,
            dual_cross_attention=False,
            use_linear_projection=False,
            upcast_attention=False,

            use_motion_module=None,
            motion_module_type=None,
            motion_module_kwargs=None,
    ):
        super().__init__()

        self.has_cross_attention = True
        self.attn_num_head_channels = attn_num_head_channels
        resnet_groups = resnet_groups if resnet_groups is not None else min(in_channels // 4, 32)

        # there is always at least one resnet
        resnets = [
            ResnetBlock2D(
                in_channels=in_channels,
                out_channels=in_channels,
                temb_channels=temb_channels,
                eps=resnet_eps,
                groups=resnet_groups,
                dropout=dropout,
                time_embedding_norm=resnet_time_scale_shift,
                non_linearity=resnet_act_fn,
                output_scale_factor=output_scale_factor,
                pre_norm=resnet_pre_norm,
            )
        ]
        attentions = []
        motion_modules = []

        for _ in range(num_layers):
            if dual_cross_attention: raise NotImplementedError
            attentions.append(
                Transformer2DModel(
                    attn_num_head_channels,
                    in_channels // attn_num_head_channels,
                    in_channels=in_channels,
                    num_layers=1,
                    cross_attention_dim=cross_attention_dim,
                    norm_num_groups=resnet_groups,
                    use_linear_projection=use_linear_projection,
                    upcast_attention=upcast_attention,
                )
            )
            motion_modules.append(
                get_motion_module(
                    in_channels=in_channels,
                    motion_module_type=motion_module_type,
                    motion_module_kwargs=motion_module_kwargs,
                ) if use_motion_module else None
            )
            resnets.append(
                ResnetBlock2D(
                    in_channels=in_channels,
                    out_channels=in_channels,
                    temb_channels=temb_channels,
                    eps=resnet_eps,
                    groups=resnet_groups,
                    dropout=dropout,
                    time_embedding_norm=resnet_time_scale_shift,
                    non_linearity=resnet_act_fn,
                    output_scale_factor=output_scale_factor,
                    pre_norm=resnet_pre_norm,
                )
            )

        self.attentions = nn.ModuleList(attentions)
        self.resnets = nn.ModuleList(resnets)
        self.motion_modules = nn.ModuleList(motion_modules) if use_motion_module else motion_modules

    def forward(self, hidden_states, temb=None, encoder_hidden_states=None, attention_mask=None,
                motion_module_alpha=1., cross_attention_kwargs=None, motion_cross_attention_kwargs=None):
        video_length = hidden_states.shape[2]
        temb_repeated = repeat(temb, "b c -> (b f) c", f=video_length)

        hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
        hidden_states = self.resnets[0](hidden_states, temb_repeated)
        hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

        lora_scale = getattr(self, "lora_scale", None)
        if lora_scale != None:
            cross_attention_kwargs = {"scale": lora_scale}
        motion_lora_scale = getattr(self, "motion_lora_scale", None)
        if motion_lora_scale != None:
            if motion_cross_attention_kwargs is None:
                motion_cross_attention_kwargs = {"scale": motion_lora_scale}
            else:
                motion_cross_attention_kwargs.update({"scale": motion_lora_scale})

        for attn, resnet, motion_module in zip(self.attentions, self.resnets[1:], self.motion_modules):
            hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
            hidden_states = attn(hidden_states, encoder_hidden_states=encoder_hidden_states,
                                 cross_attention_kwargs=cross_attention_kwargs).sample
            hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

            # motion module
            if motion_module is not None:
                # hidden_states = motion_module_alpha * motion_module(hidden_states, temb=temb, encoder_hidden_states=encoder_hidden_states) + hidden_states
                hidden_states = motion_module(hidden_states, temb=temb, encoder_hidden_states=encoder_hidden_states, cross_attention_kwargs=motion_cross_attention_kwargs)

            hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
            hidden_states = resnet(hidden_states, temb_repeated)
            hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

        return hidden_states


class CrossAttnDownBlock3D(nn.Module):
    def __init__(
            self,
            in_channels: int,
            out_channels: int,
            temb_channels: int,
            dropout: float = 0.0,
            num_layers: int = 1,
            resnet_eps: float = 1e-6,
            resnet_time_scale_shift: str = "default",
            resnet_act_fn: str = "swish",
            resnet_groups: int = 32,
            resnet_pre_norm: bool = True,
            attn_num_head_channels=1,
            cross_attention_dim=1280,
            output_scale_factor=1.0,
            downsample_padding=1,
            add_downsample=True,
            dual_cross_attention=False,
            use_linear_projection=False,
            only_cross_attention=False,
            upcast_attention=False,

            use_motion_module=None,
            motion_module_type=None,
            motion_module_kwargs=None,
    ):
        super().__init__()
        resnets = []
        attentions = []
        motion_modules = []

        self.has_cross_attention = True
        self.attn_num_head_channels = attn_num_head_channels

        for i in range(num_layers):
            in_channels = in_channels if i == 0 else out_channels
            resnets.append(
                ResnetBlock2D(
                    in_channels=in_channels,
                    out_channels=out_channels,
                    temb_channels=temb_channels,
                    eps=resnet_eps,
                    groups=resnet_groups,
                    dropout=dropout,
                    time_embedding_norm=resnet_time_scale_shift,
                    non_linearity=resnet_act_fn,
                    output_scale_factor=output_scale_factor,
                    pre_norm=resnet_pre_norm,
                )
            )

            if dual_cross_attention:
                raise NotImplementedError
            attentions.append(
                Transformer2DModel(
                    attn_num_head_channels,
                    out_channels // attn_num_head_channels,
                    in_channels=out_channels,
                    num_layers=1,
                    cross_attention_dim=cross_attention_dim,
                    norm_num_groups=resnet_groups,
                    use_linear_projection=use_linear_projection,
                    only_cross_attention=only_cross_attention,
                    upcast_attention=upcast_attention,
                )
            )
            motion_modules.append(
                get_motion_module(
                    in_channels=out_channels,
                    motion_module_type=motion_module_type,
                    motion_module_kwargs=motion_module_kwargs,
                ) if use_motion_module else None
            )

        self.attentions = nn.ModuleList(attentions)
        self.resnets = nn.ModuleList(resnets)
        self.motion_modules = nn.ModuleList(motion_modules) if use_motion_module else motion_modules

        if add_downsample:
            self.downsamplers = nn.ModuleList(
                [
                    Downsample2D(
                        out_channels, use_conv=True, out_channels=out_channels, padding=downsample_padding, name="op"
                    )
                ]
            )
        else:
            self.downsamplers = None

        self.gradient_checkpointing = False

    def forward(self, hidden_states, temb=None, encoder_hidden_states=None, attention_mask=None,
                motion_module_alpha=1., cross_attention_kwargs={}, motion_cross_attention_kwargs={}):
        video_length = hidden_states.shape[2]
        temb_repeated = repeat(temb, "b c -> (b f) c", f=video_length)

        output_states = ()

        lora_scale = getattr(self, "lora_scale", None)
        if lora_scale != None:
            cross_attention_kwargs["scale"] = lora_scale
        motion_lora_scale = getattr(self, "motion_lora_scale", None)
        if motion_lora_scale != None:
            if motion_cross_attention_kwargs is None:
                motion_cross_attention_kwargs = {"scale": motion_lora_scale}
            else:
                motion_cross_attention_kwargs.update({"scale": motion_lora_scale})

        for resnet, attn, motion_module in zip(self.resnets, self.attentions, self.motion_modules):
            if self.training and self.gradient_checkpointing:
                raise NotImplementedError

                def create_custom_forward(module, return_dict=None):
                    def custom_forward(*inputs):
                        if return_dict is not None:
                            return module(*inputs, return_dict=return_dict)
                        else:
                            return module(*inputs)

                    return custom_forward

                hidden_states = torch.utils.checkpoint.checkpoint(create_custom_forward(resnet), hidden_states, temb)
                hidden_states = torch.utils.checkpoint.checkpoint(
                    create_custom_forward(attn, return_dict=False),
                    hidden_states,
                    encoder_hidden_states,
                )[0]
                if motion_module is not None:
                    hidden_states = torch.utils.checkpoint.checkpoint(create_custom_forward(motion_module),
                                                                      hidden_states.requires_grad_(), temb,
                                                                      encoder_hidden_states)

            else:
                hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
                hidden_states = resnet(hidden_states, temb_repeated)
                hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

                hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
                hidden_states = attn(hidden_states, encoder_hidden_states=encoder_hidden_states,
                                     cross_attention_kwargs=cross_attention_kwargs).sample
                hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

                # motion module
                if motion_module is not None:
                    # hidden_states = motion_module_alpha * motion_module(hidden_states, temb=temb, encoder_hidden_states=encoder_hidden_states) + hidden_states
                    hidden_states = motion_module(hidden_states, temb=temb, encoder_hidden_states=encoder_hidden_states, cross_attention_kwargs=motion_cross_attention_kwargs)

            output_states += (hidden_states,)

        if self.downsamplers is not None:
            for downsampler in self.downsamplers:
                hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
                hidden_states = downsampler(hidden_states)
                hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

            output_states += (hidden_states,)

        return hidden_states, output_states


class DownBlock3D(nn.Module):
    def __init__(
            self,
            in_channels: int,
            out_channels: int,
            temb_channels: int,
            dropout: float = 0.0,
            num_layers: int = 1,
            resnet_eps: float = 1e-6,
            resnet_time_scale_shift: str = "default",
            resnet_act_fn: str = "swish",
            resnet_groups: int = 32,
            resnet_pre_norm: bool = True,
            output_scale_factor=1.0,
            add_downsample=True,
            downsample_padding=1,

            use_motion_module=None,
            motion_module_type=None,
            motion_module_kwargs=None,
    ):
        super().__init__()
        resnets = []
        motion_modules = []

        for i in range(num_layers):
            in_channels = in_channels if i == 0 else out_channels
            resnets.append(
                ResnetBlock2D(
                    in_channels=in_channels,
                    out_channels=out_channels,
                    temb_channels=temb_channels,
                    eps=resnet_eps,
                    groups=resnet_groups,
                    dropout=dropout,
                    time_embedding_norm=resnet_time_scale_shift,
                    non_linearity=resnet_act_fn,
                    output_scale_factor=output_scale_factor,
                    pre_norm=resnet_pre_norm,
                )
            )
            motion_modules.append(
                get_motion_module(
                    in_channels=out_channels,
                    motion_module_type=motion_module_type,
                    motion_module_kwargs=motion_module_kwargs,
                ) if use_motion_module else None
            )

        self.resnets = nn.ModuleList(resnets)
        self.motion_modules = nn.ModuleList(motion_modules) if use_motion_module else motion_modules

        if add_downsample:
            self.downsamplers = nn.ModuleList(
                [
                    Downsample2D(
                        out_channels, use_conv=True, out_channels=out_channels, padding=downsample_padding, name="op"
                    )
                ]
            )
        else:
            self.downsamplers = None

        self.gradient_checkpointing = False

    def forward(self, hidden_states, temb=None, encoder_hidden_states=None, motion_module_alpha=1.,
                motion_cross_attention_kwargs={}, **kwargs):
        video_length = hidden_states.shape[2]
        temb_repeated = repeat(temb, "b c -> (b f) c", f=video_length)
        output_states = ()
        motion_lora_scale = getattr(self, "motion_lora_scale", None)
        if motion_lora_scale != None:
            if motion_cross_attention_kwargs is None:
                motion_cross_attention_kwargs = {"scale": motion_lora_scale}
            else:
                motion_cross_attention_kwargs.update({"scale": motion_lora_scale})

        for resnet, motion_module in zip(self.resnets, self.motion_modules):
            if self.training and self.gradient_checkpointing:
                raise NotImplementedError

                def create_custom_forward(module):
                    def custom_forward(*inputs):
                        return module(*inputs)

                    return custom_forward

                hidden_states = torch.utils.checkpoint.checkpoint(create_custom_forward(resnet), hidden_states, temb)
                if motion_module is not None:
                    hidden_states = torch.utils.checkpoint.checkpoint(create_custom_forward(motion_module),
                                                                      hidden_states.requires_grad_(), temb,
                                                                      encoder_hidden_states)
            else:
                hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
                hidden_states = resnet(hidden_states, temb_repeated)
                hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

                # motion module
                if motion_module is not None:
                    hidden_states = motion_module(hidden_states, temb=temb, encoder_hidden_states=encoder_hidden_states, cross_attention_kwargs=motion_cross_attention_kwargs)

            output_states += (hidden_states,)

        if self.downsamplers is not None:
            for downsampler in self.downsamplers:
                hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
                hidden_states = downsampler(hidden_states)
                hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

            output_states += (hidden_states,)

        return hidden_states, output_states


class CrossAttnUpBlock3D(nn.Module):
    def __init__(
            self,
            in_channels: int,
            out_channels: int,
            prev_output_channel: int,
            temb_channels: int,
            dropout: float = 0.0,
            num_layers: int = 1,
            resnet_eps: float = 1e-6,
            resnet_time_scale_shift: str = "default",
            resnet_act_fn: str = "swish",
            resnet_groups: int = 32,
            resnet_pre_norm: bool = True,
            attn_num_head_channels=1,
            cross_attention_dim=1280,
            output_scale_factor=1.0,
            add_upsample=True,
            dual_cross_attention=False,
            use_linear_projection=False,
            only_cross_attention=False,
            upcast_attention=False,

            use_motion_module=None,
            motion_module_type=None,
            motion_module_kwargs=None,
    ):
        super().__init__()
        resnets = []
        attentions = []
        motion_modules = []

        self.has_cross_attention = True
        self.attn_num_head_channels = attn_num_head_channels

        for i in range(num_layers):
            res_skip_channels = in_channels if (i == num_layers - 1) else out_channels
            resnet_in_channels = prev_output_channel if i == 0 else out_channels

            resnets.append(
                ResnetBlock2D(
                    in_channels=resnet_in_channels + res_skip_channels,
                    out_channels=out_channels,
                    temb_channels=temb_channels,
                    eps=resnet_eps,
                    groups=resnet_groups,
                    dropout=dropout,
                    time_embedding_norm=resnet_time_scale_shift,
                    non_linearity=resnet_act_fn,
                    output_scale_factor=output_scale_factor,
                    pre_norm=resnet_pre_norm,
                )
            )

            if dual_cross_attention:
                raise NotImplementedError
            attentions.append(
                Transformer2DModel(
                    attn_num_head_channels,
                    out_channels // attn_num_head_channels,
                    in_channels=out_channels,
                    num_layers=1,
                    cross_attention_dim=cross_attention_dim,
                    norm_num_groups=resnet_groups,
                    use_linear_projection=use_linear_projection,
                    only_cross_attention=only_cross_attention,
                    upcast_attention=upcast_attention,
                )
            )
            motion_modules.append(
                get_motion_module(
                    in_channels=out_channels,
                    motion_module_type=motion_module_type,
                    motion_module_kwargs=motion_module_kwargs,
                ) if use_motion_module else None
            )

        self.attentions = nn.ModuleList(attentions)
        self.resnets = nn.ModuleList(resnets)
        self.motion_modules = nn.ModuleList(motion_modules) if use_motion_module else motion_modules

        if add_upsample:
            self.upsamplers = nn.ModuleList([Upsample2D(out_channels, use_conv=True, out_channels=out_channels)])
        else:
            self.upsamplers = None

        self.gradient_checkpointing = False

    def forward(
            self,
            hidden_states,
            res_hidden_states_tuple,
            temb=None,
            encoder_hidden_states=None,
            upsample_size=None,
            attention_mask=None,
            motion_module_alpha=1.,
            cross_attention_kwargs=None,
            motion_cross_attention_kwargs={}
    ):
        video_length = hidden_states.shape[2]
        temb_repeated = repeat(temb, "b c -> (b f) c", f=video_length)

        lora_scale = getattr(self, "lora_scale", None)
        if lora_scale != None:
            cross_attention_kwargs = {"scale": lora_scale}
        motion_lora_scale = getattr(self, "motion_lora_scale", None)
        if motion_lora_scale != None:
            if motion_cross_attention_kwargs is None:
                motion_cross_attention_kwargs = {"scale": motion_lora_scale}
            else:
                motion_cross_attention_kwargs.update({"scale": motion_lora_scale})

        for resnet, attn, motion_module in zip(self.resnets, self.attentions, self.motion_modules):
            # pop res hidden states
            res_hidden_states = res_hidden_states_tuple[-1]
            res_hidden_states_tuple = res_hidden_states_tuple[:-1]
            hidden_states = torch.cat([hidden_states, res_hidden_states], dim=1)

            if self.training and self.gradient_checkpointing:
                raise NotImplementedError

                def create_custom_forward(module, return_dict=None):
                    def custom_forward(*inputs):
                        if return_dict is not None:
                            return module(*inputs, return_dict=return_dict)
                        else:
                            return module(*inputs)

                    return custom_forward

                hidden_states = torch.utils.checkpoint.checkpoint(create_custom_forward(resnet), hidden_states, temb)
                hidden_states = torch.utils.checkpoint.checkpoint(
                    create_custom_forward(attn, return_dict=False),
                    hidden_states,
                    encoder_hidden_states,
                )[0]
                if motion_module is not None:
                    hidden_states = torch.utils.checkpoint.checkpoint(create_custom_forward(motion_module),
                                                                      hidden_states.requires_grad_(), temb,
                                                                      encoder_hidden_states)

            else:
                hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
                hidden_states = resnet(hidden_states, temb_repeated)
                hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

                hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
                hidden_states = attn(hidden_states, encoder_hidden_states=encoder_hidden_states,
                                     cross_attention_kwargs=cross_attention_kwargs).sample
                hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

                # motion module
                if motion_module is not None:
                    # hidden_states = motion_module_alpha * motion_module(hidden_states, temb=temb, encoder_hidden_states=encoder_hidden_states) + hidden_states
                    hidden_states = motion_module(hidden_states, temb=temb, encoder_hidden_states=encoder_hidden_states, cross_attention_kwargs=motion_cross_attention_kwargs)

        if self.upsamplers is not None:
            for upsampler in self.upsamplers:
                hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
                hidden_states = upsampler(hidden_states, upsample_size)
                hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

        return hidden_states


class UpBlock3D(nn.Module):
    def __init__(
            self,
            in_channels: int,
            prev_output_channel: int,
            out_channels: int,
            temb_channels: int,
            dropout: float = 0.0,
            num_layers: int = 1,
            resnet_eps: float = 1e-6,
            resnet_time_scale_shift: str = "default",
            resnet_act_fn: str = "swish",
            resnet_groups: int = 32,
            resnet_pre_norm: bool = True,
            output_scale_factor=1.0,
            add_upsample=True,

            use_motion_module=None,
            motion_module_type=None,
            motion_module_kwargs=None,
    ):
        super().__init__()
        resnets = []
        motion_modules = []

        for i in range(num_layers):
            res_skip_channels = in_channels if (i == num_layers - 1) else out_channels
            resnet_in_channels = prev_output_channel if i == 0 else out_channels

            resnets.append(
                ResnetBlock2D(
                    in_channels=resnet_in_channels + res_skip_channels,
                    out_channels=out_channels,
                    temb_channels=temb_channels,
                    eps=resnet_eps,
                    groups=resnet_groups,
                    dropout=dropout,
                    time_embedding_norm=resnet_time_scale_shift,
                    non_linearity=resnet_act_fn,
                    output_scale_factor=output_scale_factor,
                    pre_norm=resnet_pre_norm,
                )
            )
            motion_modules.append(
                get_motion_module(
                    in_channels=out_channels,
                    motion_module_type=motion_module_type,
                    motion_module_kwargs=motion_module_kwargs,
                ) if use_motion_module else None
            )

        self.resnets = nn.ModuleList(resnets)
        self.motion_modules = nn.ModuleList(motion_modules) if use_motion_module else motion_modules

        if add_upsample:
            self.upsamplers = nn.ModuleList([Upsample2D(out_channels, use_conv=True, out_channels=out_channels)])
        else:
            self.upsamplers = None

        self.gradient_checkpointing = False

    def forward(self, hidden_states, res_hidden_states_tuple, temb=None, upsample_size=None, encoder_hidden_states=None,
                motion_module_alpha=1., motion_cross_attention_kwargs={}, **kwargs):
        video_length = hidden_states.shape[2]
        temb_repeated = repeat(temb, "b c -> (b f) c", f=video_length)

        motion_lora_scale = getattr(self, "motion_lora_scale", None)
        if motion_lora_scale != None:
            if motion_cross_attention_kwargs is None:
                motion_cross_attention_kwargs = {"scale": motion_lora_scale}
            else:
                motion_cross_attention_kwargs.update({"scale": motion_lora_scale})

        for resnet, motion_module in zip(self.resnets, self.motion_modules):
            # pop res hidden states
            res_hidden_states = res_hidden_states_tuple[-1]
            res_hidden_states_tuple = res_hidden_states_tuple[:-1]
            hidden_states = torch.cat([hidden_states, res_hidden_states], dim=1)

            if self.training and self.gradient_checkpointing:
                raise NotImplementedError

                def create_custom_forward(module):
                    def custom_forward(*inputs):
                        return module(*inputs)

                    return custom_forward

                hidden_states = torch.utils.checkpoint.checkpoint(create_custom_forward(resnet), hidden_states, temb)
                if motion_module is not None:
                    hidden_states = torch.utils.checkpoint.checkpoint(create_custom_forward(motion_module),
                                                                      hidden_states.requires_grad_(), temb,
                                                                      encoder_hidden_states)
            else:
                hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
                hidden_states = resnet(hidden_states, temb_repeated)
                hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

                # motion module
                if motion_module is not None:
                    hidden_states = motion_module(hidden_states, temb=temb, encoder_hidden_states=encoder_hidden_states, cross_attention_kwargs=motion_cross_attention_kwargs)

        if self.upsamplers is not None:
            for upsampler in self.upsamplers:
                hidden_states = rearrange(hidden_states, "b c f h w -> (b f) c h w")
                hidden_states = upsampler(hidden_states, upsample_size)
                hidden_states = rearrange(hidden_states, "(b f) c h w -> b c f h w", f=video_length)

        return hidden_states

